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
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

from src.utils.paths import repo_root as _repo_root_fn  # noqa: E402
from src.utils.json_notes import dump_capped  # noqa: E402
from src.utils.json_notes import load_notes as _load_notes  # noqa: E402
from src.utils.closed_at import normalize_closed_at_value  # noqa: E402
from src.runtime import alert_cooldown as _alert_cooldown  # noqa: E402
from src.runtime import bybit_leg_sides as _bybit_leg_sides  # noqa: E402
from src.runtime.monitor_verdict import (  # noqa: E402
    KIND_MODIFY, KIND_PARTIAL_CLOSE, MEANINGFUL_MODIFY_REL_TOL,
    interpret_verdict,
)
_REPO_ROOT = Path(_repo_root_fn())

# BL-20260722-XRP-SLSPAM: minimum relative change (0.05% of the current sl/tp)
# a modify verdict must clear before the modify branch acts on it at all — see
# the gate in the "Modification — sl / tp" section below. Loose on purpose:
# real ATR-scaled trail steps are far larger than this; it exists only to
# absorb float/live-candle noise on ticks where price hasn't genuinely moved.
#
# Now DEFINED in src/runtime/monitor_verdict.py and re-exported here under its
# original private name: the backtest harness applies the same gate, and two
# copies of a tolerance is how the two paths drift. See that module for the P2
# rationale (it is the shared interpreter of a monitor() verdict).
_MEANINGFUL_MODIFY_REL_TOL = MEANINGFUL_MODIFY_REL_TOL


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


_STRATEGY_CFG_CACHE: Dict[str, Any] = {"mtime": None, "cfgs": {}}


def _load_live_strategy_cfgs() -> Dict[str, Dict[str, Any]]:
    """Per-strategy cfg dicts from ``config/strategies.yaml`` (mtime-cached).

    M20 E3: a YAML-declared exit lever (``stale_exit_bars``,
    ``exit_head_*``) must reach ``monitor()`` for ALREADY-OPEN packages —
    package meta is frozen at signal time, so a lever declared mid-hold
    would otherwise only cover trades opened after the flip. monitor()
    implementations keep preferring meta for frozen entry-time params
    (atr, trail_mult); cfg is the live-YAML fallback. Best-effort: any
    load error returns the previous cache (or ``{}``), never raises.
    """
    path = _REPO_ROOT / "config" / "strategies.yaml"
    try:
        mtime = path.stat().st_mtime
        if _STRATEGY_CFG_CACHE["mtime"] == mtime:
            return _STRATEGY_CFG_CACHE["cfgs"]
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        cfgs = data.get("strategies") or {}
        if isinstance(cfgs, dict):
            cfgs = {str(k): v for k, v in cfgs.items() if isinstance(v, dict)}
            _STRATEGY_CFG_CACHE.update(mtime=mtime, cfgs=cfgs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: strategies.yaml unavailable: %s", exc)
    return _STRATEGY_CFG_CACHE["cfgs"]


def _resolve_db(db_path: Optional[str]):
    """Build a Database instance for the configured journal path."""
    from src.units.db.database import Database
    from src.utils.paths import trade_journal_db_path
    path = db_path or trade_journal_db_path()
    return Database(db_path=path)


def _call_strategy_monitor(strategy_name: str, cfg: dict, candles_df,
                           open_pkg: dict) -> Tuple[Optional[Dict[str, Any]], str]:
    """Import the strategy module and call its monitor() hook.

    Returns ``(verdict, status)``. ``status`` is ``"ok"`` when ``monitor()``
    actually RAN (the ``verdict`` may still be ``None`` — a healthy
    ran-no-action tick); otherwise it is a *blindness reason* —
    ``"module_unavailable"`` / ``"no_monitor"`` / ``"raised"`` — so the caller
    can distinguish a position whose dynamic exit couldn't run (exit-coverage
    Phase 3 monitor-blindness surfacing) from one where the strategy simply had
    no opinion this tick. Never raises.
    """
    try:
        # Resolve aliased strategies (WS-A metals / M15 sleeves, ict_scalp_5m,
        # …) to the unit MODULE that owns their monitor(); a plain strategy is
        # its own module. Without this an aliased strategy's positions would
        # never be actively monitored (no same-name module) and would run on
        # static SL/TP alone — see pipeline.monitor_unit_for.
        try:
            from src.runtime.pipeline import monitor_unit_for
            module_name = monitor_unit_for(strategy_name)
        except Exception:  # noqa: BLE001 — fall back to the same-name module
            module_name = strategy_name
        mod = importlib.import_module(f"src.units.strategies.{module_name}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: strategy module %r unavailable: %s",
            strategy_name, exc,
        )
        return None, "module_unavailable"
    monitor_fn = getattr(mod, "monitor", None)
    if monitor_fn is None:
        return None, "no_monitor"
    try:
        return monitor_fn(cfg, candles_df, open_pkg), "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: %s.monitor() raised on pkg %s: %s",
            strategy_name, open_pkg.get("order_package_id"), exc,
        )
        return None, "raised"


# Exit-coverage Phase 3 — monitor-blindness surfacing. A position's PRIMARY
# (dynamic) exit is its strategy ``monitor()``; the broker SL/TP is only a
# backstop. When ``monitor()`` can't run — module unresolvable, no monitor(),
# it raised, or candles were unavailable so it couldn't evaluate — that exit is
# "blind". One blind tick is normal (a transient candle gap); PERSISTENT
# blindness means the position is riding on its static backstop alone and must
# be surfaced as a real alert, not a silent log. In-process per-package state
# (keyed by order_package_id); a restart re-arms from scratch.
_MONITOR_BLINDNESS: Dict[str, Dict[str, Any]] = {}


def _monitor_blind_alert_ticks() -> int:
    """Consecutive blind monitor ticks before alerting (tuning knob, not an
    enable gate — alerting is always on). ``MONITOR_BLINDNESS_ALERT_TICKS``,
    default 3, floored at 1."""
    try:
        return max(1, int(os.environ.get("MONITOR_BLINDNESS_ALERT_TICKS", "3")))  # allow-silent: observe-only alert-cadence knob, never gates the risk/live order path
    except (TypeError, ValueError):
        return 3


def _track_monitor_blindness(
    *, pkg_id, strategy, symbol, blind: bool, reason: str,
) -> None:
    """Track per-package consecutive blind monitor ticks and emit a one-shot
    alert when blindness persists past the threshold. A healthy (non-blind) tick
    resets the counter. Observe-only — never touches the order path, never
    raises.
    """
    if not pkg_id:
        return
    key = str(pkg_id)
    if not blind:
        _MONITOR_BLINDNESS.pop(key, None)
        return
    st = _MONITOR_BLINDNESS.get(key) or {"count": 0, "alerted": False}
    st["count"] = int(st.get("count", 0)) + 1
    st["reason"] = reason
    if st["count"] >= _monitor_blind_alert_ticks() and not st.get("alerted"):
        st["alerted"] = True
        try:
            from src.runtime.execution_diagnostics import (
                enqueue_monitor_blindness_alert,
            )
            enqueue_monitor_blindness_alert(
                order_package_id=key, strategy=str(strategy or "?"),
                symbol=str(symbol or "?"), reason=str(reason),
                consecutive_ticks=int(st["count"]),
            )
        except Exception:  # noqa: BLE001 — alerting must never break the loop
            pass
        logger.warning(
            "order_monitor: MONITOR BLIND — pkg=%s strategy=%s symbol=%s has "
            "had no live dynamic exit for %d ticks (reason=%s); riding on the "
            "static backstop only",
            key, strategy, symbol, st["count"], reason,
        )
    _MONITOR_BLINDNESS[key] = st


# ---------------------------------------------------------------------------
# P2 — unsupported-management-op log throttle
# ---------------------------------------------------------------------------
#
# When a strategy's verdict targets an integration that doesn't implement that
# op today (the senders now return ``unsupported_op:<op>`` — see
# ``account_supports_management`` + the three ``_send_*_to_exchange`` helpers),
# the failure is BENIGN and EXPECTED (the entry bracket / reverse-reconcile
# still cover the position; full management wiring is P3). Logging it at ERROR
# every monitor tick is pure spam — e.g. MGC #2597's IB-live trailing-stop
# modify error-looped ``no_client`` every tick for ~2 days.
#
# So an ``unsupported_op`` failure is logged at most ONCE per
# ``(order_package_id, op)`` in-process, at WARNING (a real condition the
# operator should see — a live trade whose dynamic management can't reach the
# exchange — but not a per-tick alarm). A genuine failure on a SUPPORTED
# integration (e.g. a Bybit retCode error) is NOT routed through here and keeps
# logging at ERROR every tick, unchanged. In-process set; a restart re-arms.
_UNSUPPORTED_OP_LOGGED: set[tuple[str, str]] = set()


def _is_unsupported_op_error(err_str: Any) -> bool:
    """True when an exchange-sender error string is the P2 ``unsupported_op``
    sentinel (the integration doesn't implement that op today)."""
    return str(err_str or "").startswith("unsupported_op")


def _note_unsupported_management_op(
    *, pkg_id: Any, op: str, account_id: Any, integration: Any, err_str: Any,
) -> None:
    """Log an ``unsupported_op`` management failure at most once per
    ``(pkg_id, op)`` at WARNING. Idempotent + never raises; the second and
    later ticks for the same (pkg, op) are silent."""
    key = (str(pkg_id or "?"), str(op or "?"))
    if key in _UNSUPPORTED_OP_LOGGED:
        return
    _UNSUPPORTED_OP_LOGGED.add(key)
    logger.warning(
        "order_monitor: %s verdict can't reach the exchange — integration "
        "%r has no wired %s (account=%s pkg=%s error=%s). Leaving the DB "
        "unchanged; the entry bracket / reconciler still cover the position. "
        "Management wiring for this integration is deferred to P3. "
        "(throttled: logged once per pkg+op)",
        op, integration, op, account_id, pkg_id, err_str,
    )


def _prov_unmeasured_marker() -> str:
    """Return :data:`provenance.UNMEASURED_MARKER`, fail-safe.

    M39(A), 2026-08-24. Used by the close path to declare *"we closed this
    trade and could not establish an exit price"* rather than writing nothing,
    which would be indistinguishable from a row nobody ever looked at.

    Imported lazily and defensively for the same reason every other
    provenance import in this module is: this runs on the LIVE close path, and
    a provenance-vocabulary import failure must never be able to prevent a
    trade from closing. The literal fallback is the same string the module
    defines; if the two ever diverge, `provenance.classify` puts an
    unrecognised source in UNVERIFIED, which is the correct bucket for this
    state anyway — so the failure mode degrades to the honest answer rather
    than to a false one.
    """
    try:
        from src.runtime.provenance import UNMEASURED_MARKER
        return str(UNMEASURED_MARKER)
    except Exception:  # noqa: BLE001 - never block a close on a doc import
        return "unmeasured"


def _apply_partial_close(
    db,
    open_pkg: dict,
    verdict: Dict[str, Any],
    summary: _StrategyTickSummary,
) -> None:
    """Partial-close path for ``close_qty_pct < 1.0`` verdicts.

    Exchange-first ordering (FU-20260515-002, 2026-05-15): mirrors the
    fix applied to ``_apply_update``'s full-close branch in PR #1190.
    Pre-this-PR the partial-close path was DB-only and a strategy that
    emitted a partial verdict (e.g. turtle_soup TP1 partial_close_pct=
    0.25) would mark the trade row down in size while leaving the full
    Bybit position open; the exchange-side SL/TP eventually closed the
    full original size at SL or TP. The new order is:

      1. Look up the linked ``trades`` row (read-only).
      2. Compute the qty to close from the verdict pct against the
         stored ``original_position_size``.
      3. Call ``_send_partial_close_to_exchange`` — short-circuits to
         ok=True on dry-run accounts.
      4. On ok=False, log ERROR, do NOT touch the DB, count an error,
         return so the next monitor tick re-attempts.
      5. On ok=True, look up the actual filled qty + avg price via
         ``account_order_status`` (FU-20260515-002 Gap A) and update
         the DB with those instead of the verdict-projected values.

    Behaviour preserved from the legacy DB-only path:

    * Appends a fragment to ``notes.partial_closes``:
      ``{"qty": pct, "reason": str, "ts": iso, "filled_qty": float,
         "exit_price": float?, "exit_price_source": "exchange"|"verdict",
         "exchange_order_id": str?}``.
    * Stores ``notes.original_position_size`` on the first partial so
      subsequent calls can compute the remaining fraction correctly.
    * Updates ``trades.position_size`` by subtracting the actual
      ``filled_qty`` from the current value (falling back to the
      verdict-requested qty when the order-status lookup is
      unavailable, e.g. dry-run accounts).
    * Keeps ``order_packages.status = 'open'``.
    * When the verdict carries a ``next_tp`` float, also rolls the
      ``order_packages.tp`` field forward so the next monitor tick
      compares price against the new target (e.g. TP2 after a TP1
      partial).
    * When cumulative closed pct >= 1.0 (sequential partials totalling
      100 %), falls through to ``_full_close_trade_and_package`` —
      which assumes the exchange close has already happened. So the
      cumulative-100% leg still attempts an exchange close first;
      only when that succeeds do the DB rows flip.
    * No-op (warning logged) when there is no linked trade row or when
      ``linked_trade_id`` is absent (the fallback symbol/strategy match
      is intentionally skipped for partial closes to avoid wrong-row
      updates).
    """
    pkg_id = open_pkg.get("order_package_id")
    close_qty_pct = float((verdict or {}).get("close_qty_pct", 1.0))
    reason = str((verdict or {}).get("reason") or "partial_close")
    verdict_exit_price = (verdict or {}).get("exit_price")
    next_tp = (verdict or {}).get("next_tp")
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
    current_pos = float(trade.get("position_size") or 0.0)
    partials: list = list(trade_notes.get("partial_closes") or [])
    already_closed_pct = sum(float(p.get("qty", 0)) for p in partials)
    new_total_closed = already_closed_pct + close_qty_pct

    # Exchange-first: dispatch the partial close BEFORE any DB write.
    # ``_send_partial_close_to_exchange`` short-circuits to ok=True on
    # dry-run accounts so paper trading still books the journal-side
    # partial. The verdict pct is applied against the ORIGINAL position
    # size so cumulative partial pcts always sum against the same base.
    requested_qty = round(original_pos * close_qty_pct, 8)
    # Whole-share venues (e.g. alpaca) can only close a whole number of shares,
    # and the broker floors the close order to a whole share — so quantize the
    # requested qty here too, otherwise ``new_position_size`` (current − filled)
    # is left a fractional remainder the broker never holds (the partial-close
    # analogue of BL-20260622-ALPACA-FRACTIONAL-SIZE — the entry path was fixed
    # via ``whole_units`` but this exit path was not). A sub-half-share close
    # rounds to 0 and is skipped by the guard below (can't close a fraction of a
    # share). Fail-open: a cfg-read error never blocks the close.
    try:
        if requested_qty > 0:
            from src.units.accounts.risk import (
                requires_whole_unit_qty,
                whole_unit_qty,
            )

            _acct_cfg = _load_account_cfgs_for_reconcile().get(
                str(trade.get("account_id") or "")
            )
            if _acct_cfg and requires_whole_unit_qty(_acct_cfg.get("exchange")):
                requested_qty = whole_unit_qty(requested_qty)  # min_one=False → may be 0
    except Exception:  # noqa: BLE001 — quantization is best-effort, never blocks
        pass
    if requested_qty <= 0:
        logger.warning(
            "order_monitor: partial-close requested qty <= 0 for pkg=%s "
            "(original_pos=%.8f close_qty_pct=%.4f) — skipping",
            pkg_id, original_pos, close_qty_pct,
        )
        summary.no_change_count += 1
        return

    ex_result = _send_partial_close_to_exchange(trade, requested_qty)
    logger.info(
        "order_monitor: exchange partial close for pkg=%s account=%s "
        "qty=%.8f → %s",
        pkg_id, trade.get("account_id"), requested_qty, ex_result,
    )

    if not ex_result.get("ok"):
        # Exchange refused or errored. Do NOT touch the DB so the
        # next monitor tick re-attempts and the strategy-monocle gate
        # continues to suppress duplicate signals.
        err_str = ex_result.get("error") or "unknown"
        if _is_unsupported_op_error(err_str):
            # P2: integration doesn't implement partial_close today — log once
            # per (pkg, op) at WARNING instead of an ERROR every tick. DB stays
            # open (same as a genuine failure); P3 wires the op.
            _note_unsupported_management_op(
                pkg_id=pkg_id, op="partial_close",
                account_id=trade.get("account_id"),
                integration=ex_result.get("integration"), err_str=err_str,
            )
        else:
            logger.error(
                "order_monitor: exchange partial close failed — leaving DB open. "
                "pkg=%s account=%s symbol=%s qty=%s error=%s",
                pkg_id, trade.get("account_id"),
                trade.get("symbol"), requested_qty, err_str,
            )
        summary.error_count += 1
        summary.errors.append(
            f"{pkg_id}: exchange partial close failed: {err_str}"
        )
        return

    # Exchange ack (or dry-run short-circuit). Look up the actual fill
    # details via account_order_status so the DB reflects what really
    # filled (rounded for lot-size, partial fills, slippage). When the
    # lookup fails or returns no avg_price, fall back to the verdict's
    # projected exit_price + the requested qty and annotate the
    # fragment so consumers can distinguish "exchange-confirmed" from
    # "verdict-projected" fills downstream.
    fill_details = _capture_fill_details(
        trade, ex_result.get("exchange_order_id"),
    )
    if fill_details is not None and fill_details.get("filled_qty"):
        actual_filled_qty = float(fill_details["filled_qty"])
        actual_exit_price: Optional[float] = float(fill_details["avg_price"])
        exit_price_source = "exchange"
    else:
        actual_filled_qty = float(requested_qty)
        actual_exit_price = (
            float(verdict_exit_price) if verdict_exit_price is not None else None
        )
        exit_price_source = "verdict"

    fragment: Dict[str, Any] = {
        "qty": close_qty_pct,
        "reason": reason,
        "ts": now,
        "filled_qty": actual_filled_qty,
        "exit_price_source": exit_price_source,
    }
    if actual_exit_price is not None:
        fragment["exit_price"] = actual_exit_price
    if ex_result.get("exchange_order_id"):
        fragment["exchange_order_id"] = str(ex_result["exchange_order_id"])
    partials.append(fragment)

    if new_total_closed >= 1.0:
        # Sequential partials reached/exceeded 100 % — fall through to
        # the shared full-close helper. The exchange close has already
        # landed above, so the helper only writes the DB.
        trade_notes["partial_closes"] = partials
        if "original_position_size" not in trade_notes:
            trade_notes["original_position_size"] = original_pos
        _full_close_trade_and_package(
            db,
            pkg_id=pkg_id,
            linked_trade_id=int(linked_trade_id),
            reason=reason,
            exit_price=actual_exit_price,
            extra_notes=trade_notes,
            summary=summary,
        )
        return

    # True partial: reduce position_size, keep package open.
    if "original_position_size" not in trade_notes:
        trade_notes["original_position_size"] = original_pos
    trade_notes["partial_closes"] = partials

    new_position_size = max(0.0, round(current_pos - actual_filled_qty, 8))

    try:
        db.update_trade(int(linked_trade_id), {
            "position_size": new_position_size,
            "notes": dump_capped(trade_notes, 2000),
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: partial-close trade write failed pkg=%s trade=%s: %s",
            pkg_id, linked_trade_id, exc,
        )
        summary.error_count += 1
        summary.errors.append(f"{pkg_id}: partial-close trade-write failed")
        return

    # Roll the package's tp forward when the verdict supplied next_tp
    # (e.g. turtle_soup emits next_tp=meta.tp2 alongside a TP1 partial).
    # Failure here is non-fatal — the partial close has already landed
    # and the next tick will retry against the stale tp at worst.
    if next_tp is not None:
        try:
            db.update_order_package(pkg_id, {"tp": float(next_tp)})
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order_monitor: partial-close next_tp write failed pkg=%s: %s",
                pkg_id, exc,
            )

    logger.info(
        "order_monitor: partial close pkg=%s trade=%s "
        "close_pct=%.3f filled_qty=%.8f new_position_size=%.8f "
        "exit_price=%s exit_price_source=%s next_tp=%s",
        pkg_id, linked_trade_id, close_qty_pct, actual_filled_qty,
        new_position_size, actual_exit_price, exit_price_source, next_tp,
    )
    summary.updated_count += 1

    # Trade-lifecycle update ping (TELEGRAM-SPEC §4.2) — best-effort. The
    # partial close is already booked; a ping failure must never affect it.
    try:
        from src.runtime.execution_diagnostics import enqueue_trade_update

        changes = [
            f"Partial close {close_qty_pct:.0%} (filled {actual_filled_qty:g})",
            f"New size {new_position_size:g}",
        ]
        if actual_exit_price is not None:
            changes.append(f"Exit {actual_exit_price:g}")
        if next_tp is not None:
            changes.append(f"TP rolled to {next_tp}")
        enqueue_trade_update(
            symbol=trade.get("symbol") or "?",
            account=trade.get("account_id"),
            strategy=trade.get("strategy_name") or trade.get("setup_type"),
            changes=changes,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: trade-update (partial) ping failed for trade=%s: %s",
            linked_trade_id, exc,
        )


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
        closed_at_iso = datetime.now(timezone.utc).isoformat()
        close_updates: Dict[str, Any] = {
            "status": "closed",
            "exit_reason": reason,
            "closed_at": closed_at_iso,
        }
        if exit_price is not None:
            close_updates["exit_price"] = float(exit_price)
        if extra_notes:
            # Read-modify-write the notes field.
            rows = db.get_trades(filters={"id": linked_trade_id})
            existing_notes = _decode_notes(rows[0].get("notes") if rows else None)
            existing_notes.update(extra_notes)
            close_updates["notes"] = dump_capped(existing_notes, 2000)
        db.update_trade(linked_trade_id, close_updates)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: trade close write failed for trade=%s pkg=%s: %s",
            linked_trade_id, pkg_id, exc,
        )

    summary.closed_count += 1

    # Trade-lifecycle close ping (TELEGRAM-SPEC §4.2) — best-effort. The
    # close is already recorded; a ping failure must never affect it.
    try:
        from src.runtime.execution_diagnostics import enqueue_trade_close

        rows = db.get_trades(filters={"id": linked_trade_id})
        row = rows[0] if rows else {}
        enqueue_trade_close(
            symbol=row.get("symbol") or "?",
            account=row.get("account_id"),
            strategy=row.get("strategy_name") or row.get("setup_type"),
            entry=row.get("entry_price"),
            exit_price=exit_price if exit_price is not None else row.get("exit_price"),
            pnl=row.get("pnl"),
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: trade-close ping failed for trade=%s: %s",
            linked_trade_id, exc,
        )


def _package_open_legs(db, open_pkg: dict) -> tuple:
    """Every OPEN, non-backtest trade row belonging to this order package.

    Returns ``(legs, read_state)``. ``read_state`` is never collapsed:

    * ``"resolved"``  — a clean read. ``legs`` is the complete set and MAY be
      empty (a package with no open trade row is a real, meaningful state).
    * ``"read_failed"`` — we could not look. ``legs`` is empty, and the caller
      must NOT treat that as "no legs": effectuating on an unconfirmed read is
      how a close gets sent to a position we never verified.

    The ``linked_trade_id`` leg is placed FIRST so single-leg packages keep
    byte-identical ordering semantics and the historical "primary" leg is still
    handled first on a multi-leg one.

    **Why this exists** (BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG).
    ``Coordinator.multi_account_execute`` fans ONE package out across N
    accounts, so N trade rows share one ``order_package_id`` while
    ``linked_trade_id`` names exactly one of them. Both effectuation branches
    resolved that single id, so N-1 legs were never trailed and never closed.
    """
    pkg_id = open_pkg.get("order_package_id")
    linked = open_pkg.get("linked_trade_id")
    try:
        rows = []
        if pkg_id:
            rows = [
                r for r in db.get_trades(
                    filters={"order_package_id": pkg_id, "status": "open"})
                if not r.get("is_backtest")
            ]
        if not rows:
            # No package-scoped rows. Preserve the historical single-leg
            # fallback CONDITIONS exactly — this is not a free-for-all search.
            #
            # The old code branched: `if linked_trade_id: resolve by id` ELSE
            # `_find_trade_by_match(strategy, symbol)`. Falling through to the
            # strategy+symbol match when a linked id EXISTS but its row is
            # closed would resurrect a DIFFERENT package's open trade on the
            # same strategy+symbol and effectuate this package's verdict
            # against it. So the match is reachable only when the package
            # names no linked leg at all, exactly as before.
            if linked:
                hit = db.get_trades(filters={"id": int(linked)})
                rows = ([hit[0]] if hit and hit[0].get("status") == "open"
                        else [])
            else:
                m = _find_trade_by_match(
                    db, strategy=open_pkg.get("strategy_name"),
                    symbol=open_pkg.get("symbol"),
                )
                rows = [m] if m else []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: package-leg lookup failed for pkg=%s: %s",
            pkg_id, exc,
        )
        return [], "read_failed"

    if linked is not None:
        rows.sort(key=lambda r: str(r.get("id")) != str(linked))
    return rows, "resolved"


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
    # What the verdict MEANS is decided by the one shared interpreter
    # (src/runtime/monitor_verdict.py); this function owns only the
    # effectuation (exchange + DB). The backtest harness calls the same
    # interpreter with an in-memory effectuator, which is what makes exit-path
    # fidelity structural instead of two hand-maintained copies.
    decision = interpret_verdict(verdict,
                                 current_sl=open_pkg.get("sl"),
                                 current_tp=open_pkg.get("tp"))
    if decision.rejection is not None:
        # `no_meaningful_change` is the expected steady state of a trail
        # recomputing off a forming candle — noisy to warn on every tick.
        # Every other rejection is a verdict the strategy MEANT to act on.
        if decision.rejection != "no_meaningful_change":
            logger.warning(
                "order_monitor: verdict not actionable (%s): %r for pkg %s",
                decision.rejection, verdict, pkg_id,
            )
        summary.no_change_count += 1
        return
    if decision.kind == KIND_PARTIAL_CLOSE:
        # Partial-close path was originally DB-only. The 2026-05-15
        # exchange-first refactor (FU-20260515-002) reordered it to dispatch
        # to the exchange before any DB write, mirroring the full-close branch
        # fixed in PR #1190.
        _apply_partial_close(db, open_pkg, verdict, summary)
        return
    if decision.is_close:
        # NB `close_qty_pct == 1.0` resolves to a FULL close, as before.
        reason = decision.reason or "monitor_close"

        # 2026-05-15: exchange-first close ordering. Pre-this-PR the
        # DB rows were flipped to ``status='closed'`` and a fabricated
        # PnL was stamped BEFORE the live exchange call, so any
        # exchange failure (Bug 1 / 170131, network blips, rate limits)
        # left the journal lying about a still-open position and the
        # reverse-reconciler then adopted the live position as a
        # duplicate ``adopted_orphan`` row. The new order is:
        #
        #   1. Look up the matched trade row (read-only).
        #   2. Attempt ``_send_close_to_exchange`` — short-circuits to
        #      ok=True on dry-run accounts.
        #   3. On ok=True, write package close + trade close + PnL.
        #      Increment ``summary.closed_count``.
        #   4. On ok=False, log ERROR, do NOT touch the DB, count an
        #      error, and return so the next monitor tick re-attempts.
        #
        # The legacy comment block about the deleted
        # ``MONITOR_APPLY_TO_EXCHANGE`` shadow-mode gate (operator
        # directive 2026-05-03) is kept below in the modify branch
        # for history.

        # 2026-08-18 (BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG): resolve
        # over EVERY open leg of the package, not just `linked_trade_id`. One
        # package fans out across N accounts; closing the linked leg and then
        # flipping the PARENT to `closed` dropped the survivors out of the
        # loop's `status="open"` selection permanently. Measured: 6 of 35 open
        # trades were stranded that way.
        #
        # We still close ONE leg per tick, deliberately. The alternative —
        # closing all N in this pass — turns one exchange round-trip per
        # package into N on a loop already breaching its 60s requirement, and
        # piling concurrent closes onto one shared IB socket is the shape of
        # both June 2026 wedges. Leaving the package OPEN below when legs
        # remain makes the next tick pick up the next leg, so the package
        # drains over N ticks with the per-tick cost unchanged.
        legs, leg_read_state = _package_open_legs(db, open_pkg)
        if leg_read_state != "resolved":
            # An unconfirmed read is NOT "no legs". Never close on it.
            logger.error(
                "order_monitor: package-leg read FAILED for pkg=%s — refusing "
                "to close on an unconfirmed read; retrying next tick.", pkg_id,
            )
            summary.error_count += 1
            summary.errors.append(f"{pkg_id}: leg read failed")
            return
        matched_trade = legs[0] if legs else None

        # No trade row → nothing to close on the exchange. Still flip
        # the package status so the strategy-monocle gate clears. This
        # preserves the prior behaviour for the "package without a
        # linked trade" case (e.g. exchange_rejected at entry where
        # the package was never paired with a live position).
        if matched_trade is None:
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
            summary.closed_count += 1
            return

        # Close-retry cooldown (BL-20260624-MHG-CLOSE-CONFIRM, follow-up). When a
        # prior close was ACCEPTED but never confirmed flat (IBClient.close →
        # retCode 1 "not confirmed flat" — e.g. a venue that can't fill right now:
        # market closed for the contract, gateway mid-reset), re-attempting the
        # close every tick would cancel the re-armed protective bracket (close()
        # Step 1) and place another non-filling order — churn that leaves the
        # position briefly naked each tick and keeps cancelling the very stop that
        # would flatten it when the venue reopens. While cooling down we DEFER the
        # active close and leave the bracket armed to do the job; the marker is set
        # below on an unconfirmed close and cleared on a confirmed one.
        # ``IB_CLOSE_RETRY_COOLDOWN_S <= 0`` disables (retry every tick).
        _close_key = (
            str(matched_trade.get("account_id") or ""),
            str(matched_trade.get("symbol") or ""),
            str(matched_trade.get("direction") or "").lower(),
        )
        _cooldown_s = _close_retry_cooldown_seconds()
        if _cooldown_s > 0:
            _last_unconfirmed = _PENDING_CLOSE_RETRY_COOLDOWN.get(_close_key)
            if _last_unconfirmed is not None:
                _age = (datetime.now(timezone.utc) - _last_unconfirmed).total_seconds()
                if _age < _cooldown_s:
                    logger.info(
                        "order_monitor: close retry cooling down for %s "
                        "(%.0fs/%ss since last unconfirmed close) — bracket left "
                        "armed, deferring active close. pkg=%s",
                        _close_key, _age, _cooldown_s, pkg_id,
                    )
                    summary.no_change_count += 1
                    return
                # Window elapsed → drop the stale marker and retry the close.
                _PENDING_CLOSE_RETRY_COOLDOWN.pop(_close_key, None)

        # Mark this (account, symbol) as actively-closing THIS tick so the
        # broker-naked equity re-arm (which runs later in the same tick) does not
        # re-place a protective OCO on a position we're trying to flatten — that
        # fight is BL-20260708-ALPACA-REARM-VS-CLOSE-FIGHT (see the set's comment).
        mark_active_close(
            matched_trade.get("account_id"), matched_trade.get("symbol"),
        )

        # Exchange-first: attempt the live close BEFORE any DB write.
        ex_result = _send_close_to_exchange(matched_trade)
        logger.info(
            "order_monitor: exchange close for pkg=%s account=%s → %s",
            pkg_id, matched_trade.get("account_id"), ex_result,
        )
        if not ex_result.get("ok"):
            err_str = ex_result.get("error") or "unknown"
            # Market-session DEFER (BL-20260716-ALPACA-MARKET-HOURS-EXIT).
            # AlpacaClient.close returns a "deferred, not failed" signal
            # (retCode 2, message says "exit deferred" / "deferring") when the US
            # equity market is closed, or an extended-hours limit close is still
            # working. The position simply CANNOT be flattened until the session
            # (re)opens — so this is a quiet no-change, NOT a failure: no
            # consecutive-close-failure streak, no "won't flatten" alarm. The
            # protective bracket (closed) / working limit (extended) handles the
            # exit; the monitor re-attempts next tick.
            if ("exit deferred" in err_str.lower()
                    or "deferring" in err_str.lower()
                    or "market closed" in err_str.lower()):
                logger.info(
                    "order_monitor: exchange close DEFERRED (market session) "
                    "for pkg=%s account=%s → %s — DB left open, no alarm.",
                    pkg_id, matched_trade.get("account_id"), err_str,
                )
                _clear_close_fail_alert_state(_close_key)  # a defer clears the streak
                summary.no_change_count += 1
                return
            # Bybit signals "position already gone" with retCode 30031
            # (position size is zero) or 110017 / 110025. When the
            # exchange's internal SL/TP fires before the monitor's close
            # attempt, we get one of these. Treat as exchange-closed so
            # the DB is updated and the reconciler doesn't have to clean
            # up a stale open row.
            _already_closed = (
                "position size is zero" in err_str.lower()
                or "retCode=30031" in err_str
                or "retCode=110017" in err_str
                or "retCode=110025" in err_str
            )
            if _already_closed:
                logger.info(
                    "order_monitor: exchange reports position already closed "
                    "(SL/TP fired before monitor) — proceeding with DB update. "
                    "pkg=%s account=%s error=%s",
                    pkg_id, matched_trade.get("account_id"), err_str,
                )
                # Fall through to DB update as if exchange close succeeded.
                ex_result = {"ok": True, "skipped": "already_closed_on_exchange",
                             "exchange_response": None, "exchange_order_id": None,
                             "error": None}
            elif _is_unsupported_op_error(err_str):
                # P2: integration doesn't implement close today — log once per
                # (pkg, op) at WARNING instead of an ERROR every tick. DB stays
                # open (same as a genuine failure); P3 wires the op.
                _note_unsupported_management_op(
                    pkg_id=pkg_id, op="close",
                    account_id=matched_trade.get("account_id"),
                    integration=ex_result.get("integration"), err_str=err_str,
                )
                summary.error_count += 1
                summary.errors.append(f"{pkg_id}: exchange close failed: {err_str}")
                return
            else:
                # Exchange refused for an unrecognised reason. Leave DB open
                # so the next monitor tick re-attempts.
                logger.error(
                    "order_monitor: exchange close failed — leaving DB open. "
                    "pkg=%s account=%s symbol=%s qty=%s error=%s",
                    pkg_id, matched_trade.get("account_id"),
                    matched_trade.get("symbol"),
                    matched_trade.get("position_size"),
                    err_str,
                )
                # Accepted-but-unfilled close (the position is still open) →
                # arm the close-retry cooldown so we don't churn the protective
                # bracket every tick. Scoped to this signature so a transient
                # error (e.g. a one-off network blip) still retries next tick.
                if "not confirmed flat" in err_str.lower():
                    _PENDING_CLOSE_RETRY_COOLDOWN[_close_key] = datetime.now(timezone.utc)
                # Item #3: count consecutive close failures for this
                # (account, symbol, direction) and ALERT once the streak crosses
                # the threshold (and every threshold-th failure after) — a
                # position that won't flatten is no longer retried silently
                # forever. Cleared on a confirmed close.
                _streak = _CLOSE_FAIL_STREAK.get(_close_key, 0) + 1
                _CLOSE_FAIL_STREAK[_close_key] = _streak
                if _should_alert_close_failure(_close_key, _streak):
                    try:
                        from src.runtime.execution_diagnostics import (
                            enqueue_close_failure,
                        )
                        enqueue_close_failure(
                            account=matched_trade.get("account_id"),
                            symbol=matched_trade.get("symbol"),
                            side=matched_trade.get("direction"),
                            qty=matched_trade.get("position_size"),
                            consecutive=_streak, error=err_str,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "order_monitor: close-failure alert enqueue failed "
                            "pkg=%s: %s", pkg_id, exc,
                        )
                summary.error_count += 1
                summary.errors.append(f"{pkg_id}: exchange close failed: {err_str}")
                return

        # Confirmed close (or dry-run skip) → clear the close-retry cooldown
        # marker AND the consecutive-failure streak for this (account, symbol,
        # direction) so a future close isn't needlessly deferred / re-alerted.
        _PENDING_CLOSE_RETRY_COOLDOWN.pop(_close_key, None)
        _clear_close_fail_alert_state(_close_key)

        # Exchange close ok (or dry-run skip). Capture the actual fill
        # price from Bybit before writing the DB so the trade row's
        # exit_price + PnL reflect what really filled, not the
        # verdict's projected close price (FU-20260515-002).
        #
        # Bybit's ``place_order`` response doesn't include a fill price,
        # so the lookup hits ``account_order_status`` against the
        # returned ``exchange_order_id``. Read-failure / not-found
        # falls back to ``verdict.exit_price`` and the notes field
        # records ``exit_price_source="verdict"`` so consumers can tell
        # exchange-confirmed exit_prices apart from projected ones —
        # the reverse_reconciler is the SSOT for delayed reconciliation
        # if the first-attempt avg_price is stale.
        fill_details = _capture_fill_details(
            matched_trade, ex_result.get("exchange_order_id"),
        )
        if fill_details is not None and fill_details.get("avg_price"):
            actual_exit_price: Optional[float] = float(fill_details["avg_price"])
            exit_price_source = "exchange"
        else:
            verdict_exit_price = (verdict or {}).get("exit_price")
            actual_exit_price = (
                float(verdict_exit_price) if verdict_exit_price is not None else None
            )
            exit_price_source = "verdict"

        # DB writes. The historical order was package-close → trade-close;
        # since 2026-08-18 the trade closes FIRST so the remaining-leg check
        # below sees post-close truth. The exchange call has already succeeded
        # at this point, so neither order risks a journal that claims a
        # still-open position is closed.
        try:
            closed_at_iso = datetime.now(timezone.utc).isoformat()
            close_updates: Dict[str, Any] = {
                "status": "closed",
                "exit_reason": reason,
                "closed_at": closed_at_iso,
            }
            if actual_exit_price is not None:
                close_updates["exit_price"] = actual_exit_price
            # ── STAMP EVERY OUTCOME, INCLUDING THE GOOD ONE ───────────────
            # M39(A), 2026-08-24. Backlog row (kept on ONE line so the id
            # actually resolves — a wrapped id reads as a dangling reference,
            # which `check_backlog_refs` correctly refuses):
            # BL-20260824-THE-DECIDED-EXIT-PATH-IS-THE-UNMEASURED-ONE
            # This block used to write the note ONLY under
            # `exit_price_source == "verdict"`, so the branch that resolved a
            # REAL venue fill stamped nothing and the row classified as
            # UNVERIFIED — the strongest evidence we ever have, recorded as
            # "we don't know". Measured on the live journal before the fix:
            # of 175 unverified DECIDED closes, 175 (100%) carried an
            # `exit_price` and ZERO carried a stamp, and the string
            # `"exchange"` appeared 0 times in the whole decided-close source
            # distribution — this branch had never written a note, ever.
            #
            # `"exchange"` needs no vocabulary change: it is already in
            # `provenance.MEASURED_SOURCES` and is what this module's own
            # docstring declares (see the `_apply_update` contract above,
            # `"exit_price_source": "exchange"|"verdict"`). It is genuine
            # broker truth — `_capture_fill_details` returns only on a
            # non-zero `avg_price` from `account_order_status`, and yields
            # None for dry-run / read-failure / not-found, all of which fall
            # to the verdict branch. Unlike `recorded_exit_price` (demoted
            # from MEASURED the same day for claiming a fill it never had),
            # this one IS the venue's own number.
            #
            # THIRD STATE, deliberately: a close with no price on EITHER
            # branch used to be skipped as "nothing meaningful to source-tag".
            # It is meaningful — it says we closed the trade and could not
            # establish what price we got. Silence makes that identical to a
            # row nobody ever looked at, which is the collapse
            # `docs/CLAUDE-RULES-CANONICAL.md` § "Collapsed states" exists to
            # forbid. `UNMEASURED_MARKER` shares the UNVERIFIED *trust* bucket
            # (neither is a measurement) but is distinguishable in
            # ACCOUNTABILITY from the raw source string — the same
            # `anchored`/`deferred`/`no_anchor` discipline `exit_anchor` uses.
            # It is not observed in the current journal (0 of 175); it is
            # here so the state cannot become silent later.
            #
            # NEVER overwrites a more specific existing stamp — the sibling
            # rule added to `_sweep_local_pnl_for_unpriced` on 2026-08-24,
            # after an unconditional overwrite laundered a projection over a
            # broker source.
            if actual_exit_price is not None:
                _exit_source = exit_price_source
            else:
                _exit_source = _prov_unmeasured_marker()
            existing_notes = _decode_notes(matched_trade.get("notes"))
            if not existing_notes.get("exit_price_source"):
                existing_notes["exit_price_source"] = _exit_source
                close_updates["notes"] = dump_capped(existing_notes, 2000)
            trade_id = matched_trade.get("id")
            if trade_id is not None:
                db.update_trade(int(trade_id), close_updates)
                matched_trade = {**matched_trade, **close_updates}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order_monitor: trades close-side update failed for %s: %s",
                pkg_id, exc,
            )

        # Realised-PnL booking (2026-05-18 SSOT refactor): pnl is no
        # longer computed locally at close time. The Bybit-truth sweep
        # ``_sweep_pending_pnl_from_bybit`` (invoked from
        # ``run_monitor_tick``) fills ``pnl`` / ``exit_price`` /
        # ``notes.bybit_closed_pnl`` from Bybit's
        # ``/v5/position/closed-pnl`` endpoint within a few ticks of
        # close. Until that lookup succeeds, ``pnl`` stays NULL and
        # the dashboard renders an em-dash for the row. This deletes
        # the historical fee-blind gross-PnL write that produced
        # silent dashboard / Bybit discrepancies (e.g. trade #1540's
        # gross +$1.03 vs Bybit's net of fees).
        # (Was: ``_compute_close_pnl(matched_trade, actual_exit_price)``
        # followed by ``db.update_trade(trade_id, pnl_updates)``.)

        # Flip the PARENT only when no open leg remains. This is the whole
        # repair: the loop selects packages by `status="open"`, so flipping it
        # while a sibling leg is still open removes that leg from the exit path
        # for good. Re-read rather than deducing from `legs` — the row we just
        # closed is one of them, and another tick or the reconciler may have
        # moved a sibling underneath us.
        remaining, remaining_state = _package_open_legs(db, open_pkg)
        if remaining_state != "resolved":
            # We cannot show the package is drained, so we must not say it is.
            # Leaving it OPEN is the safe direction: the next tick re-reads,
            # and a package with nothing left simply closes then.
            logger.warning(
                "order_monitor: closed trade %s but could not re-read pkg=%s "
                "legs — leaving the package OPEN; next tick re-checks.",
                matched_trade.get("id"), pkg_id,
            )
            summary.closed_count += 1
            return
        if remaining:
            logger.info(
                "order_monitor: closed leg %s (%s) for pkg=%s; %d open leg(s) "
                "remain (%s) — package stays OPEN so the next tick closes the "
                "next one.",
                matched_trade.get("id"), matched_trade.get("account_id"),
                pkg_id, len(remaining),
                ", ".join(f"{r.get('id')}/{r.get('account_id')}"
                          for r in remaining),
            )
            summary.closed_count += 1
            return

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

        summary.closed_count += 1
        return

    # Modification — sl / tp (other keys are silently ignored). Both are
    # applied INDEPENDENTLY; a verdict carrying both moves both.
    #
    # The interpreter has already applied BL-20260722-XRP-SLSPAM's
    # meaningful-change filter (`MEANINGFUL_MODIFY_REL_TOL`): a monitor
    # recomputing a trail off a live/forming candle returns a "new" sl/tp that
    # differs only by float noise on ticks where price hasn't genuinely moved —
    # xrp_pullback_2h/trade 3577 fired an exchange amend + a "TRADE UPDATED"
    # Telegram ping essentially every tick for 5 days off a trail the operator
    # correctly perceived as static. A fully-filtered modify arrives here as a
    # `no_meaningful_change` rejection and returned above, so reaching this
    # point means at least one key genuinely moved.
    assert decision.kind == KIND_MODIFY, decision.kind
    updates: Dict[str, Any] = {}
    if decision.sl is not None:
        updates["sl"] = decision.sl
    if decision.tp is not None:
        updates["tp"] = decision.tp

    # 2026-05-18: exchange-first modify ordering. Mirrors the close-path
    # refactor from PR #1190 + the partial-close refactor from
    # FU-20260515-002. Pre-this-PR the DB row was updated FIRST and the
    # exchange call only fired if a matching trade row was found; when
    # the lookup returned empty rows the exchange call was silently
    # skipped. Live impact: SL-to-break-even verdicts moved the DB's
    # stored SL but never reached Bybit, so the trade ran to its
    # original SL while the strategy log + dashboard showed a new one.
    # New order is symmetric with the close path:
    #
    #   1. Look up the matched trade row (read-only). Prefer the
    #      package's ``linked_trade_id``; fall back to the open
    #      trade matching strategy+symbol.
    #   2. No trade row → ERROR log + ``summary.error_count`` + return
    #      without touching the DB. The strategy will re-emit the
    #      verdict next tick once the linkage lands.
    #   3. ``_send_modify_to_exchange`` — short-circuits to ok=True
    #      on dry-run accounts.
    #   4. On ok=True, write the sl/tp updates to ``order_packages``
    #      and increment ``summary.updated_count``.
    #   5. On ok=False, log ERROR, leave the DB row untouched, count
    #      an error, and return so the next monitor tick re-attempts.

    # 2026-08-18 (BL-20260818-MONITOR-MANAGES-ONLY-THE-LINKED-LEG): a trail /
    # stale-stop / giveback must reach EVERY leg of the package, not only
    # `linked_trade_id`. Unlike the close path there is no draining to do here
    # — a modify is not terminal, so a leg skipped this tick is skipped
    # forever unless the level happens to move again. Measured: 4 of 8 live
    # multi-leg packages carried sibling stops that had never moved, in every
    # case the non-linked leg.
    #
    # An amend is idempotent and opens no position, so fanning it out does not
    # carry the close path's concurrency hazard.
    legs, leg_read_state = _package_open_legs(db, open_pkg)
    if leg_read_state != "resolved":
        logger.error(
            "order_monitor: package-leg read FAILED for pkg=%s — refusing to "
            "modify on an unconfirmed read; verdict re-fires next tick.",
            pkg_id,
        )
        summary.error_count += 1
        summary.errors.append(f"{pkg_id}: leg read failed")
        return
    matched_trade = legs[0] if legs else None

    if matched_trade is None:
        logger.error(
            "order_monitor: modify-path trade lookup returned no open row — "
            "skipping exchange modify AND leaving DB row unchanged so the "
            "verdict re-fires next tick. pkg=%s strategy=%s symbol=%s "
            "verdict_updates=%s",
            pkg_id, open_pkg.get("strategy_name"),
            open_pkg.get("symbol"), updates,
        )
        summary.error_count += 1
        summary.errors.append(f"{pkg_id}: modify-path missing trade row")
        return

    # S2 (BL-20260616-LTMGMT-MODIFY): forward the position side + size and the
    # package's CURRENT sl/tp so the IB/Alpaca re-arm path can rebuild the
    # protective bracket without dropping the leg the verdict didn't change.
    # Bybit ignores these (byte-unchanged). ``open_pkg`` carries the active
    # sl/tp this modify is about to overwrite — exactly the "unchanged leg"
    # fallback the IB OCA re-arm needs.
    def _coerce_float(value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    # Fan the amend out across every open leg. Per-leg outcome is three-state
    # and never collapsed: `applied` / `failed` / `unsupported` (the
    # integration has no modify op — a known P2 shortfall, not a live error).
    leg_outcomes: List[Dict[str, Any]] = []
    for leg in legs:
        ex_result = _send_modify_to_exchange(
            leg,
            sl=updates.get("sl"),
            tp=updates.get("tp"),
            side=leg.get("direction"),
            qty=_coerce_float(leg.get("position_size")),
            cur_sl=_coerce_float(open_pkg.get("sl")),
            cur_tp=_coerce_float(open_pkg.get("tp")),
        )
        logger.info(
            "order_monitor: exchange modify for pkg=%s trade=%s account=%s → %s",
            pkg_id, leg.get("id"), leg.get("account_id"), ex_result,
        )
        if ex_result.get("ok"):
            leg_outcomes.append({"leg": leg, "outcome": "applied"})
            continue
        err_str = ex_result.get("error") or "unknown"
        if _is_unsupported_op_error(err_str):
            # P2: integration doesn't implement modify today — log once per
            # (pkg, op) at WARNING instead of an ERROR every tick. DB is still
            # left unchanged (same as a genuine failure); P3 wires the op.
            _note_unsupported_management_op(
                pkg_id=pkg_id, op="modify",
                account_id=leg.get("account_id"),
                integration=ex_result.get("integration"), err_str=err_str,
            )
            leg_outcomes.append({"leg": leg, "outcome": "unsupported",
                                 "error": err_str})
        else:
            logger.error(
                "order_monitor: exchange modify failed — leaving DB unchanged. "
                "pkg=%s trade=%s account=%s symbol=%s sl=%s tp=%s error=%s",
                pkg_id, leg.get("id"), leg.get("account_id"),
                leg.get("symbol"), updates.get("sl"), updates.get("tp"),
                err_str,
            )
            leg_outcomes.append({"leg": leg, "outcome": "failed",
                                 "error": err_str})

    applied = [o for o in leg_outcomes if o["outcome"] == "applied"]
    not_applied = [o for o in leg_outcomes if o["outcome"] != "applied"]

    # Sync `trades.stop_loss`/`take_profit_1` for the legs that ACTUALLY
    # landed, before deciding the package write. A leg whose amend failed must
    # keep showing its real venue level — writing the intended level onto it
    # would put the dashboard back in the exact "SL looks static but the row
    # says otherwise" state BL-20260722-XRP-SLSPAM part 2 fixed, only inverted.
    for o in applied:
        try:
            trade_id = o["leg"].get("id")
            if trade_id is not None:
                trade_sync: Dict[str, Any] = {}
                if "sl" in updates:
                    trade_sync["stop_loss"] = updates["sl"]
                if "tp" in updates:
                    trade_sync["take_profit_1"] = updates["tp"]
                if trade_sync:
                    db.update_trade(int(trade_id), trade_sync)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "order_monitor: trades protective-level sync failed for pkg=%s "
                "trade_id=%s: %s", pkg_id, o["leg"].get("id"), exc,
            )

    if not_applied:
        # Leave `order_packages.sl/tp` UNCHANGED so the verdict is still a
        # meaningful change next tick and the missed legs are retried. Writing
        # it here would make the interpreter's `no_meaningful_change` filter
        # swallow the identical verdict on every later tick, and the missed leg
        # would keep its entry-time bracket forever — the very bug this change
        # exists to fix, re-created one level up.
        #
        # The accepted cost is that an already-applied leg is re-amended next
        # tick. That is idempotent at the venue; stranding a leg is not.
        detail = ", ".join(
            f"{o['leg'].get('id')}/{o['leg'].get('account_id')}:{o['outcome']}"
            for o in not_applied)
        # LOG LEVEL differs by cause; the COUNT does not — matching the
        # pre-fan-out contract exactly. An integration with no modify op is a
        # KNOWN P2 shortfall already logged once per (pkg, op) by
        # `_note_unsupported_management_op`, so an ERROR every tick would be
        # the desensitized-alarm P1 (pinned by
        # test_apply_update_modify_unsupported_logs_warning_once_no_error).
        # It is still counted, because the verdict genuinely did not land.
        if any(o["outcome"] == "failed" for o in not_applied):
            logger.error(
                "order_monitor: modify reached %d/%d legs for pkg=%s (%s) — "
                "package sl/tp left UNCHANGED so the verdict re-fires and "
                "retries the rest next tick.",
                len(applied), len(leg_outcomes), pkg_id, detail,
            )
        else:
            logger.info(
                "order_monitor: modify reached %d/%d legs for pkg=%s (%s) — "
                "the rest are unsupported by their integration; package sl/tp "
                "left unchanged.",
                len(applied), len(leg_outcomes), pkg_id, detail,
            )
        summary.error_count += 1
        summary.errors.append(
            f"{pkg_id}: modify reached "
            f"{len(applied)}/{len(leg_outcomes)} legs")
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

    # BL-20260722-XRP-SLSPAM (part 2) — mirroring the confirmed modify onto
    # the ``trades`` row — now happens PER LEG in the loop above, before the
    # package write, because only the legs whose amend actually landed may have
    # their stored level moved. (That fix exists because a modify used to touch
    # only ``order_packages.sl/tp`` while /api/bot/positions reads
    # ``trades.stop_loss``, so every operator surface kept showing the original
    # level. It uses ``stop_loss``/``take_profit_1`` — the real column names,
    # NOT ``sl``/``tp`` — which also keeps it clear of ``update_trade``'s own
    # notify-gate so it cannot double-fire against the ping below.)

    # Trade-lifecycle update ping (SL/TP move, TELEGRAM-SPEC §4.2) —
    # best-effort. The modify already landed; a ping failure can't affect it.
    try:
        from src.runtime.execution_diagnostics import enqueue_trade_update

        changes = []
        if "sl" in updates:
            changes.append(f"SL → {updates['sl']:g}")
        if "tp" in updates:
            changes.append(f"TP → {updates['tp']:g}")
        # ONE ping per package, not per leg — N pings for a single trail move
        # is the desensitized-alarm shape. But say how many legs moved, so a
        # multi-account package does not read as a single-leg one.
        if len(applied) > 1:
            changes.append(f"({len(applied)} legs)")
        enqueue_trade_update(
            symbol=matched_trade.get("symbol") or "?",
            account=matched_trade.get("account_id"),
            strategy=matched_trade.get("strategy_name")
            or matched_trade.get("setup_type"),
            changes=changes,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: trade-update (modify) ping failed for pkg=%s: %s",
            pkg_id, exc,
        )


# ``_compute_close_pnl`` was deleted on 2026-05-18 as part of the
# SSOT PnL refactor (#1400's sibling). The local gross-PnL formula it
# implemented (``(exit - entry) * size`` for longs, mirror for shorts)
# was fee-blind and produced silent discrepancies between the dashboard
# and Bybit's account view — e.g. trade #1540 closed via tp_cross
# recorded +$1.03 gross while Bybit's actual net (after taker fees on
# both sides) was ~+$0.57. The single source of PnL is now Bybit's
# ``/v5/position/closed-pnl`` endpoint, reached via
# ``account_closed_pnl_for_trade`` and applied by either:
#   * the inline reconciler path (DB-open / exchange-flat orphan) in
#     ``_close_trade_from_order_status``; or
#   * the post-close pending-pnl sweep ``_sweep_pending_pnl_from_bybit``
#     for any trade that was closed by the monitor (tp_cross, monitor
#     SL, partial close) — Bybit's record typically lands within 30-60s
#     of the close, so this sweep usually completes within one tick.
# The dashboard treats ``pnl IS NULL`` as "pending" (em-dash) per the
# Position-shape contract in CLAUDE.md.


def _find_trade_by_match(db, *, strategy: Optional[str],
                         symbol: Optional[str]) -> Optional[Dict[str, Any]]:
    """Read-only: return the most-recent open trade row matching the
    strategy + symbol (or ``None`` if none). Used by the exchange-first
    close path in ``_apply_update`` to look up the trade row's
    ``account_id`` + ``position_size`` BEFORE attempting the live
    close. The companion ``_close_trade_by_match`` keeps its
    find-and-update semantics for any caller that still wants the
    legacy combined behaviour.
    """
    if not strategy or not symbol:
        return None
    try:
        rows = db.get_trades(
            filters={
                "strategy_name": strategy,
                "symbol": symbol,
                "status": "open",
            },
            limit=1,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: _find_trade_by_match read failed for %s/%s: %s",
            strategy, symbol, exc,
        )
        return None
    return rows[0] if rows else None


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
# Exchange-side wiring (S-030 PR4) — dry/live decided per-account by ``mode:``
# (the ``MONITOR_APPLY_TO_EXCHANGE`` shadow-mode gate was removed; the senders
# short-circuit only on ``mode == "dry_run"``, never on an env flag).
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
            bybit_client_for,
            ib_client_for, alpaca_client_for, oanda_client_for,
        )
        for acc in load_accounts():
            if acc.name != account_id:
                continue
            cfg = {
                "account_id": acc.name,
                "exchange": acc.exchange,
                "api_key_env": acc.api_key_env,
                # Companion secret env-var NAME — the CLOSE/manage-path analogue
                # of the coordinator fix (BL-20260701-ALPACA-LIVE-SECRET-ENV).
                # An account that names its own key pair (alpaca_live →
                # ALPACA_API_SECRET_KEY_LIVE) must pair the live KEY with the
                # live SECRET here too; without it alpaca_client_for falls back
                # to the shared paper secret and the close path 401s on a live
                # account exactly as the entry path did.
                "api_secret_env": getattr(acc, "api_secret_env", None),
                # Without this, _bybit_category() in execute.py defaults
                # to "spot" and the close path sends spot reduceOnly to a
                # linear account → Bybit 170131. See FU-20260515-001.
                "market_type": getattr(acc, "market_type", None) or "spot",
                # 2026-05-15: surface the per-account mode so the
                # exchange-side wiring (``_send_close_to_exchange``,
                # ``_send_modify_to_exchange``) can short-circuit on
                # paper accounts without ever calling ``place_order``.
                "mode": getattr(acc, "mode", "live") or "live",
                # Required by bybit_client_for() to route demo accounts to
                # api-demo.bybit.com instead of api.bybit.com.
                "demo": getattr(acc, "demo", False),
                # IB connection identity (no API keys — auth is the Gateway
                # login session). ib_client_for() reads these from the cfg
                # to build the socket; None for non-IB accounts. Required so
                # the P3 IB close path (_send_close_to_exchange) can reach
                # the gateway.
                "ib_host": getattr(acc, "ib_host", None),
                "ib_port": getattr(acc, "ib_port", None),
                "ib_account": getattr(acc, "ib_account", None),
                "ib_client_id": getattr(acc, "ib_client_id", None),
                # Optional Alpaca host override (these steer paper vs live;
                # the KEY/SECRET env-var NAMES ride in api_key_env /
                # api_secret_env above — alpaca_client_for resolves the values
                # from those names, so both must be forwarded).
                "alpaca_env": getattr(acc, "alpaca_env", None),
                "base_url": getattr(acc, "base_url", None),
                # Optional OANDA host override (oanda_client_for reads the
                # token + account id from env directly; this only steers
                # practice vs live). Needed so the S2 OANDA close path can
                # reach the v20 API once oanda_practice leaves dry_run.
                "oanda_env": getattr(acc, "oanda_env", None),
            }
            exchange_lc = (acc.exchange or "").lower()
            if exchange_lc == "bybit":
                return bybit_client_for(cfg), cfg
            # P3 (live-trade management contract): build the IB / Alpaca
            # clients too so the verdict senders can reach them for close.
            # Reuses the SAME factories _submit_order uses at entry, so the
            # management path and the entry path share one client model.
            if exchange_lc in ("interactive_brokers", "ib"):
                return ib_client_for(cfg), cfg
            if exchange_lc == "alpaca":
                return alpaca_client_for(cfg), cfg
            # S2 (BL-20260616-LTMGMT-OANDA): build the OANDA client too so the
            # close verdict reaches the v20 API before oanda_practice goes live.
            if exchange_lc == "oanda":
                return oanda_client_for(cfg), cfg
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

    Dry-run short-circuit (2026-05-15): when the resolved cfg has
    ``mode == "dry_run"`` the helper returns
    ``{"ok": True, "skipped": "dry_run", ...}`` WITHOUT calling
    ``close_open_position``. This is the single dry/live toggle for
    monitor-driven closes and lets the caller's exchange-first flow
    proceed with the DB updates exactly as a live success would.

    ``matched_trade``'s ``sl_order_id`` / ``tp_order_id`` are forwarded so
    Bybit can best-effort cancel this trade's own tracked Partial-tpsl
    leg(s) after a confirmed close (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP) —
    ``None`` on pre-migration / Full-mode rows, a harmless no-op there.
    """
    try:
        from src.units.accounts.execute import close_open_position
        from src.units.accounts.clients import account_supports_management
        client, cfg = _build_account_client(matched_trade.get("account_id"))
        # P2 management-capability gate: if this integration doesn't implement
        # ``close`` today, say so honestly (``unsupported_op``) instead of the
        # misleading ``no_client`` (the client is None because it was never
        # built, not because creds are missing). Bybit supports close so this
        # branch is never taken for it — its path below is byte-for-byte
        # unchanged. Wiring IB/Alpaca/OANDA close is P3.
        if not account_supports_management(cfg, "close"):
            return {"ok": False, "error": "unsupported_op:close",
                    "integration": (cfg or {}).get("exchange")}
        if client is None or cfg is None:
            return {"ok": False, "error": "no_client"}
        if (cfg or {}).get("mode") == "dry_run":
            return {
                "ok": True,
                "skipped": "dry_run",
                "exchange_response": None,
                "exchange_order_id": None,
                "error": None,
            }
        return close_open_position(
            client, cfg,
            symbol=matched_trade.get("symbol"),
            side=matched_trade.get("direction"),
            qty=float(matched_trade.get("position_size") or 0.0),
            sl_order_id=matched_trade.get("sl_order_id"),
            tp_order_id=matched_trade.get("tp_order_id"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: exchange close failed: %s", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _cancel_resting_protection_after_flat(
    account_id: Optional[str], symbol: Optional[str],
) -> None:
    """Cancel resting protective bracket legs for (account, symbol) after the
    RECONCILER concludes the position is flat on the exchange. Best-effort.

    The reconciler's flat-close paths (``exchange_flat_reconciled`` / snapshot /
    close-on-disappear) mark the DB row closed WITHOUT going through
    ``IBClient.close`` — the position is already flat, so no opposing order is
    sent — and so they never cancelled the symbol's resting GTC OCA legs. On IB
    those stale stops sit on a now-flat position and can later fire, SELLING into
    a reverse position → a fresh orphan (the MHG long→short flip,
    BL-20260624-MHG-FLIP). This sweeps them.

    ⚠️ **IT IS IB-ONLY, AND THE REASON THIS DOCSTRING GAVE FOR THAT IS FALSE FOR
    BYBIT** (corrected 2026-09-02, comment only — no behaviour changed here). It
    read: *"IB-only: Bybit/Alpaca/OANDA closes are atomic / position-attached, so
    there are no stranded resting legs to cancel."* Under
    ``BYBIT_TPSL_MODE=partial`` — **the live value** — a Bybit protective leg is a
    SEPARATE resting conditional order, which :func:`_bybit_position_protection`'s
    own docstring says outright two thousand lines down. **Field beats comment.**

    Only ``IBClient`` implements ``cancel_resting_protection`` (grep, whole tree),
    so this is a no-op on Bybit — and, separately, it is wired ONLY into
    :func:`_reconcile_orphan_exchange_positions`, **not** into the
    ``reconciler_filled`` close path in :func:`_reconcile_open_trades`. So a Bybit
    row whose position went flat at the venue is finalised with NOTHING cancelling
    its tracked ``trades.sl_order_id`` / ``tp_order_id`` legs: the active-close
    path's cancel lives in ``close_open_position``, which that path never calls.

    OBSERVED 2026-09-02 (trader ``git_sha 68e73de8``): ``bybit_1`` trade 5308
    (short 0.424, BTCUSDT) closed ``reconciler_filled`` at 03:05:33Z, and both of
    its tracked legs — ``c50841d7…`` SL and ``3629a331…`` TP, ids matching the
    journal exactly — were still resting on the venue at 03:41:27Z, 36 minutes
    later. MEASURED denominator: 16 of the 92 closed ``bybit%`` rows in the 200
    most-recent ``trades`` rows (Data Explorer table read,
    ``limit=200&order_by=id&order_dir=desc``, read 2026-09-02T03:50Z; the route
    itself is spelled out in ``CLAUDE.md`` — it is NOT spelled out here because
    the literal path is `collapsed-state-guard`'s ``db_explorer.*``
    consumer_token, and prose naming it makes this file a false "consumer" of a
    contract it has nothing to do with) closed ``reconciler_filled`` while carrying a tracked leg
    id — a path that cancels nothing. ⚠️ That is 16 closes on a no-cancel path,
    NOT 16 stranded legs: a leg that fired is gone, and the venue clears many
    itself. What it establishes is that the bot relies entirely on the venue
    there, and that at least one close demonstrably left its legs resting.

    Wiring a Bybit ``cancel_resting_protection`` (or extending the tracked-leg
    cancel to the reconciler path) is a **Tier-2** order-path change and is
    deliberately NOT made here — it is proposed in the PR that corrected this
    docstring, so it lands with its own review and its own tests.

    Never raises into the reconcile sweep.
    """
    if not account_id or not symbol:
        return
    try:
        client, cfg = _build_account_client(account_id)
    except Exception:  # noqa: BLE001
        return
    if client is None or cfg is None:
        return
    cancel_fn = getattr(client, "cancel_resting_protection", None)
    if not callable(cancel_fn):
        return  # non-IB integration — nothing to sweep
    try:
        resp = cancel_fn(symbol) or {}
        if resp.get("retCode") not in (0, "0", None):
            logger.info(
                "order_monitor: cancel resting protection after flat-close for "
                "%s/%s → %s", account_id, symbol, resp.get("retMsg"),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "order_monitor: cancel-resting-protection-after-flat failed for "
            "%s/%s: %s", account_id, symbol, exc,
        )


def _send_partial_close_to_exchange(
    matched_trade: Dict[str, Any], qty: float,
) -> Dict[str, Any]:
    """Send a reduce-only close order for *qty* (subset of the matched
    trade's position_size) to the exchange.

    FU-20260515-002 Gap B: companion to :func:`_send_close_to_exchange`
    for the partial-close path. ``close_open_position`` always sets
    ``reduceOnly=True`` regardless of whether qty matches the full
    position size, so the same helper handles both legs.

    Dry-run short-circuit: when the resolved cfg has ``mode ==
    "dry_run"`` the helper returns ``{"ok": True, "skipped":
    "dry_run", ...}`` WITHOUT calling ``close_open_position`` — the
    caller (``_apply_partial_close``) treats that exactly like a live
    success and writes the DB-side partial.

    Best-effort — never raises. ``client is None`` or any underlying
    exception returns ``{"ok": False, ...}`` so the caller can leave
    the DB row untouched and retry next tick.
    """
    try:
        from src.units.accounts.execute import close_open_position
        from src.units.accounts.clients import account_supports_management
        client, cfg = _build_account_client(matched_trade.get("account_id"))
        # P2 management-capability gate (see _send_close_to_exchange): honest
        # ``unsupported_op`` instead of ``no_client`` for integrations without a
        # wired partial-close. Bybit supports it → its path below is unchanged.
        if not account_supports_management(cfg, "partial_close"):
            return {"ok": False, "error": "unsupported_op:partial_close",
                    "integration": (cfg or {}).get("exchange"),
                    "exchange_order_id": None}
        if client is None or cfg is None:
            return {"ok": False, "error": "no_client",
                    "exchange_order_id": None}
        if (cfg or {}).get("mode") == "dry_run":
            return {
                "ok": True,
                "skipped": "dry_run",
                "exchange_response": None,
                "exchange_order_id": None,
                "error": None,
            }
        return close_open_position(
            client, cfg,
            symbol=matched_trade.get("symbol"),
            side=matched_trade.get("direction"),
            qty=float(qty),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: exchange partial close failed: %s", exc)
        return {"ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "exchange_order_id": None}


def _capture_fill_details(
    matched_trade: Dict[str, Any],
    exchange_order_id: Optional[str],
) -> Optional[Dict[str, float]]:
    """Look up the actual fill price + qty for *exchange_order_id*.

    FU-20260515-002 Gap A: Bybit's ``place_order`` response doesn't
    carry a fill price, so the close path used to record the
    monitor's projected ``verdict.exit_price`` as the journal's
    ``exit_price``. The reverse_reconciler eventually caught the
    discrepancy on closed trades but the journal's per-trade P&L was
    wrong in the meantime. This helper closes the gap by hitting
    ``account_order_status`` against the returned order id.

    Returns
    -------
    dict | None
        ``{"avg_price": float, "filled_qty": float}`` when the
        exchange reports a non-zero ``avg_price``. ``None`` when:

        * ``exchange_order_id`` is falsy (dry-run skip leaves it
          unset)
        * the account cfg can't be resolved (no client)
        * the account is ``mode: dry_run`` (no order to look up)
        * ``account_order_status`` returns ``None`` (read failure)
        * the exchange reports ``status="not_found"`` with zero
          ``avg_price``, even after a single short-delay retry
          (Bybit's order index lag is ~1-3 s after place_order; one
          retry catches most of it without pinning a tight loop)
        * any unexpected exception

    The caller is expected to fall back to verdict-derived values
    and annotate ``trades.notes`` with ``exit_price_source="verdict"``.
    """
    if not exchange_order_id:
        return None
    try:
        _, cfg = _build_account_client(matched_trade.get("account_id"))
        if cfg is None:
            return None
        if (cfg or {}).get("mode") == "dry_run":
            return None
        from src.units.accounts.clients import account_order_status

        status = account_order_status(cfg, str(exchange_order_id))
        # Single short-delay retry when the order is genuinely
        # "not_found" — Bybit's open-orders index typically populates
        # within ~50-200 ms but order_history can lag 1-3 s after
        # place_order. ``account_order_status`` checks both, so a
        # not_found verdict means the order hasn't landed in either
        # index yet. One retry catches that race; further failures
        # fall through to the verdict-derived fallback and the
        # reverse_reconciler picks up any lasting discrepancy.
        if status is not None:
            avg_price = float(status.get("avg_price") or 0.0)
            status_label = str(status.get("status") or "").lower()
            if avg_price <= 0.0 and status_label == "not_found":
                import time
                time.sleep(0.5)
                status = account_order_status(cfg, str(exchange_order_id))
        if status is None:
            return None
        avg_price = float(status.get("avg_price") or 0.0)
        if avg_price <= 0.0:
            return None
        return {
            "avg_price": avg_price,
            "filled_qty": float(status.get("filled_qty") or 0.0),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("_capture_fill_details: %s", exc)
        return None


def _send_modify_to_exchange(matched_trade: Dict[str, Any], *,
                             sl: Optional[float] = None,
                             tp: Optional[float] = None,
                             side: Optional[str] = None,
                             qty: Optional[float] = None,
                             cur_sl: Optional[float] = None,
                             cur_tp: Optional[float] = None) -> Dict[str, Any]:
    """Send a SL/TP modify to the exchange for the matched trade row.

    Dry-run short-circuit (2026-05-18): when the resolved cfg has
    ``mode == "dry_run"`` the helper returns
    ``{"ok": True, "skipped": "dry_run", ...}`` WITHOUT calling
    ``modify_open_order``. Mirrors ``_send_close_to_exchange`` so the
    exchange-first modify flow in ``_apply_update`` writes the DB
    exactly as a live success would on paper accounts.

    ``sl`` / ``tp`` are the verdict deltas (what changed). ``side`` / ``qty``
    (the position's side + size) and ``cur_sl`` / ``cur_tp`` (the order
    package's current levels) are forwarded for the IB/Alpaca re-arm path
    (S2, BL-20260616-LTMGMT-MODIFY) — IB needs both effective levels to
    re-arm its OCA bracket without dropping a leg. The Bybit branch of
    ``modify_open_order`` ignores them, so its path stays byte-unchanged.

    ``matched_trade``'s ``sl_order_id`` / ``tp_order_id`` (from
    ``trades``, captured at entry — BL-20260721-BYBIT2-XRP-TPSL-LEGCAP) are
    forwarded so a Bybit Partial-tpsl modify amends that SPECIFIC leg in
    place instead of adding a new one. ``None`` on pre-migration / Full-mode
    / ambiguous-capture rows — ``modify_open_order`` falls back to the
    legacy add-a-leg behaviour for those, unchanged.
    """
    try:
        from src.units.accounts.execute import modify_open_order
        from src.units.accounts.clients import account_supports_management
        client, cfg = _build_account_client(matched_trade.get("account_id"))
        # P2 management-capability gate (see _send_close_to_exchange): honest
        # ``unsupported_op`` instead of ``no_client`` for integrations without a
        # wired SL/TP modify. Bybit supports modify → its path below is
        # byte-for-byte unchanged. S2 wired IB + Alpaca modify (this is what was
        # error-looping ``no_client`` every tick for IB-live trailing-stop
        # modifies, MGC #2597); OANDA modify is still unwired.
        if not account_supports_management(cfg, "modify"):
            return {"ok": False, "error": "unsupported_op:modify",
                    "integration": (cfg or {}).get("exchange")}
        if client is None or cfg is None:
            return {"ok": False, "error": "no_client"}
        if (cfg or {}).get("mode") == "dry_run":
            return {
                "ok": True,
                "skipped": "dry_run",
                "exchange_response": None,
                "error": None,
            }
        return modify_open_order(
            client, cfg,
            symbol=matched_trade.get("symbol"),
            sl=sl, tp=tp,
            side=side, qty=qty, cur_sl=cur_sl, cur_tp=cur_tp,
            sl_order_id=matched_trade.get("sl_order_id"),
            tp_order_id=matched_trade.get("tp_order_id"),
            # Scopes the IB pre-cancel to this trade's own OCA group; ignored by
            # every non-IB branch. See modify_open_order's IB branch for why.
            trade_id=matched_trade.get("id"),
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
# Runs unconditionally every monitor tick (the MONITOR_RECONCILE_ENABLED
# gate was removed 2026-06-15, BL-20260615-MGCNAKED — self-heal is baseline
# correctness, not a feature flag).

_ORPHAN_PING_CAP = 10

# Default grace window: a freshly-placed trade is not eligible for
# orphan-stamping until ``created_at`` is at least this many seconds in
# the past. Backstop against any residual Bybit order-create race —
# the SSOT path (issue #502) does its own per-orderId lookup that is
# consistent on the create-response side, so after a few days of soak
# the operator can drop this from 60 s to ~5 s. Operator tunes via
# ``RECONCILER_GRACE_SECONDS``.
_DEFAULT_RECONCILER_GRACE_SECONDS = 60

# Position-netting guard — reconciler half (Option A, BL-20260608-DEMOPNL).
# The netting guard is now BASELINE (unconditional — the
# POSITION_NETTING_GUARD_ENABLED gate was removed 2026-06-17). A filled trade
# that reads net-flat is NOT closed on the first observation: it must read flat
# across
# an extra grace tick (a second observation, ``RECONCILER_CLOSE_CONFIRM_SECONDS``
# apart) before the close lands. A transient net-flat — an intent reduce/flip
# leg momentarily flattening the net position, or the open-positions index
# lagging — is cleared the next time the position reads open, so it can no
# longer prematurely close a row and free the strategy-monocle gate (which
# is what let the netted short keep growing on the demo account). In-process
# state (keyed by trades.id); a restart simply re-arms the confirmation from
# scratch — fail-safe (never closes early).
_DEFAULT_CLOSE_CONFIRM_SECONDS = 60
_PENDING_CLOSE_CONFIRM: Dict[int, datetime] = {}

# Close-retry cooldown (BL-20260624-MHG-CLOSE-CONFIRM follow-up). After a close
# is ACCEPTED but never confirmed flat (IBClient.close → retCode 1 "not confirmed
# flat" — a venue that can't fill right now), re-attempting the active close every
# tick would cancel the re-armed protective bracket and place another non-filling
# order. This maps (account_id, symbol, direction) → the time of the last such
# unconfirmed close; while within `IB_CLOSE_RETRY_COOLDOWN_S` the monitor defers
# the active close and leaves the bracket armed to flatten when the venue reopens.
# Cleared on a confirmed close. In-process; a restart re-arms from scratch
# (fail-safe — a fresh process simply retries the close, never closes early).
_DEFAULT_CLOSE_RETRY_COOLDOWN_SECONDS = 300
_PENDING_CLOSE_RETRY_COOLDOWN: Dict[tuple, datetime] = {}

# Consecutive monitor-close failures per (account, symbol, direction). The
# exchange-first close leaves the DB row open and retries on any exchange-close
# failure — previously a SILENT loop (ERROR log, no operator ping). After
# MONITOR_CLOSE_FAIL_ALERT_AFTER consecutive failures we alert so a position that
# won't flatten is surfaced (item #3). Cleared on a confirmed close. In-process;
# a restart re-arms from scratch.
_DEFAULT_CLOSE_FAIL_ALERT_AFTER = 3
_CLOSE_FAIL_STREAK: Dict[tuple, int] = {}

# Close-failure alert BACKOFF (BL-20260901-CLOSE-FAIL-ALARM-DESENSITISED).
# The streak threshold above decides WHEN the first page fires; these decide how
# often it may fire AFTER that. Previously the cadence was a fixed modulo
# (`_streak % _after == 0`) — tied to the tick rate, never widening, never
# capped: a position wedged for one extended-hours session pages ~160 times at
# the 30s EXIT_LOOP_INTERVAL_SECONDS default. That is the desensitised-alarm
# failure CLAUDE.md's "If you see something, say something" names outright — an
# alarm nobody can afford to read is worse than one that fires once.
#
# So paging is now TIME-based with an exponentially widening interval, floored
# at `_DEFAULT_CLOSE_FAIL_ALERT_BACKOFF_S` and clamped at
# `_DEFAULT_CLOSE_FAIL_ALERT_MAX_BACKOFF_S`. It deliberately NEVER goes silent —
# a wedged position keeps pinging at the max interval forever, because the
# failure mode this whole subsystem exists to end is the SILENT retry loop.
# Decoupled from tick rate on purpose: re-tuning EXIT_LOOP_INTERVAL_SECONDS no
# longer silently re-tunes the pager. In-process; a restart re-pages once
# immediately (fail-loud — a fresh process re-surfaces a still-wedged position).
_DEFAULT_CLOSE_FAIL_ALERT_BACKOFF_S = 300.0     # 5 min to the 2nd page
_DEFAULT_CLOSE_FAIL_ALERT_MAX_BACKOFF_S = 3600.0  # never slower than hourly
_CLOSE_FAIL_ALERT_AT: Dict[tuple, float] = {}     # key -> time.monotonic()
_CLOSE_FAIL_ALERT_COUNT: Dict[tuple, int] = {}    # key -> pages sent this streak

# Re-adopt guard window (BL-20260618-RECONCILE-DUP, 2026-06-18). When an IB
# gateway flaps (logged-out → empty portfolio → back) during the broker reset
# window, the reverse reconciler could adopt the SAME exchange position, have
# the re-attached strategy's monitor close the DB row at an sl_cross (the IB
# exchange position itself never closed), then RE-ADOPT it next pass — looping
# N times and booking N phantom losses (one MGC position became 18 closed
# trades, -$20,127). The guard refuses to re-adopt a (account, symbol,
# direction) whose ``adopted_orphan`` row closed within this window — a
# just-closed adopted orphan that reappears is a flap, not a genuinely new
# position. ``0`` disables the guard. Read at call time (next-tick effect).
#
# BL-20260618 residual hardening (2026-07-19): widened 300 → 1800 (30 min). The
# dominant real flap is the IBKR ~03:45–05:45 UTC reset window (gateway logout →
# empty portfolio → back), which can span well beyond 5 min; a 300s window let a
# slower flap re-adopt a SINGLE duplicate (the real-money double-count risk on
# bybit_2). Widening is SAFE because the guard SUPPRESSES-AND-ALERTS
# (``detect_only``), never silently strands — a false-suppress of a genuinely
# new position surfaces as an operator alert with the exchange position still
# SL-/operator-protected, so the worst case is recoverable, not lost. The adopt
# path only fires for UN-matched (orphan) positions anyway, so a real strategy
# position (which carries a journal row) never reaches this guard. A fully
# position-continuity-aware guard (track last broker-confirmed-flat per key) is
# the documented follow-up if 30 min proves insufficient.
_DEFAULT_READOPT_GUARD_SECONDS = 1800

# Fresh-fill grace (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE): the P3b
# position-snapshot reconciler closes a strategy-attributed row whose
# (symbol, side) reads absent from a SUCCESSFUL exchange snapshot. On an
# integration without a per-order status reader (alpaca/oanda), a just-placed
# bracket-MARKET order can take minutes to fill AND propagate to the
# open-positions endpoint — during that window the position is genuinely absent
# from the snapshot yet is NOT flat (it's pending fill). Without a minimum age
# the 2-observation confirm alone false-closes it (IWM/alpaca_paper trade 2771:
# closed `exchange_flat_reconciled` ~2.5 min after open, then the SAME live
# position was re-adopted as an orphan 2 min later — a close→re-adopt flap).
# A strategy-attributed trade younger than this is skipped by the snapshot-close
# pass (the close-on-disappear pass for adopted_orphan rows is unaffected — an
# adopted orphan is by definition already confirmed live on the exchange).
_DEFAULT_SNAPSHOT_MIN_FILL_AGE_SECONDS = 300

# Account-reset detection (operator-requested 2026-07-08; behaviour hardened by
# RISK-1 2026-07-09). When the position-snapshot reconciler confirms >= this
# many strategy-attributed positions on ONE account absent in a single pass,
# that is a SUSPECTED wholesale account RESET (the 2026-07-07 alpaca_paper reset
# wiped all 8 positions at once), not a set of individual disappearances.
# RISK-1 (BL-20260707-RECONCILER-MASS-FALSE-CLOSE): a suspected reset is NO
# LONGER auto-closed — mass-closing N live rows on one inference is the
# amplifier that turned a bad read into 7 false closes. It fires ONE latched
# alert and leaves the rows OPEN for the operator. 3 keeps a normal 1-2 position
# exit on the individual-close path while routing any wholesale vanish to
# alert-first.
_ACCOUNT_RESET_SNAPSHOT_THRESHOLD = 3

# Exit-coverage reattach-or-close (2026-06-15): an open ``orphan_adopt`` trade
# with NO recoverable order package has no rational exit strategy and is
# flattened. Like ``_PENDING_CLOSE_CONFIRM`` above, the flatten waits for a 2nd
# confirming observation (``_close_confirm_seconds`` apart) so a transient read
# — the originating package simply not written yet — can't trigger a spurious
# close. In-process, keyed by trades.id; a restart re-arms from scratch
# (fail-safe — never closes early). Cleared the moment a row becomes
# reattachable.
_PENDING_ORPHAN_NOSTRAT_CLOSE: Dict[int, datetime] = {}

# Reverse-reconciler half of the same idea (BL-20260614-ORPHANBLIP). The
# close-on-disappear pass in ``_reconcile_orphan_exchange_positions`` must NOT
# close an ``orphan_adopt`` row the first time the exchange snapshot omits its
# (symbol, side) — a logged-out IB Gateway can return an *empty* portfolio
# (``[]``, not a read failure → not ``None``), and a single such blip would
# close the adopted row, only for the next healthy read to re-adopt it as a new
# orphan (the MHG adopt→close→re-adopt flip-flop seen 2026-06-12..14). The
# (symbol, side) must read absent across an extra grace tick (a second
# observation) before the close lands; a snapshot that brings the position back
# clears the pending close. In-process state keyed by trades.id; a restart
# re-arms from scratch — fail-safe (never closes early). Reuses
# ``_close_confirm_seconds()`` for the grace window (a tuning knob, not an
# enable gate — the confirm is always on, per the no-third-gate Prime Directive).
_PENDING_ORPHAN_DISAPPEAR_CONFIRM: Dict[int, datetime] = {}

# Universal position-snapshot reconciliation (P3b, live-trade-management
# contract — docs/audits/live-trade-management-contract-2026-06-16.md). The
# REVERSE reconciler (above) catches exchange→DB drift (a position live on the
# exchange with no journal row). The FORWARD reconciler (_reconcile_open_trades)
# catches DB→exchange drift for integrations with a per-order status reader —
# but it short-circuits non-Bybit rows because account_order_status returns None
# for them. That left DB-open *strategy-attributed* trades on non-order-status
# integrations (IB, Alpaca — anything without the ``order_status`` management
# cap) stuck ``status='open'`` forever once their entry bracket fired / they were
# closed exchange-side: nothing reconciled them until the stuck-strategy watchdog
# eventually orphaned the row (the #2596 class).
#
# This dict arms the SAME 2-observation close-confirm the orphan_adopt
# close-on-disappear path uses, but for strategy-attributed rows on
# non-order-status integrations: a DB-open row whose ``(symbol, side)`` is absent
# from a SUCCESSFUL ``account_open_positions`` snapshot is closed only after it
# reads absent across two observations (``_close_confirm_seconds`` apart). A
# read failure (``None`` snapshot) NEVER closes and CLEARS any pending arming —
# so a transient/empty-error snapshot can't false-close a live row. Kept separate
# from the orphan_adopt dict so the two close paths can't cross-contaminate each
# other's arming state. In-process, keyed by trades.id; a restart re-arms from
# scratch (fail-safe — never closes early). The merged local-PnL sweep
# (_sweep_local_pnl_for_unpriced) realises the closed row's PnL next tick
# (mark-to-market); this path never computes PnL and never sends an exchange
# order (the position is ALREADY closed exchange-side — this is reconciliation,
# not a close-send). No kill-switch — baseline correctness per the Prime
# Directive (mirrors the always-on reverse reconciler).
_PENDING_SNAPSHOT_DISAPPEAR_CONFIRM: Dict[int, datetime] = {}

# RISK-1 (BL-20260707-RECONCILER-MASS-FALSE-CLOSE): per-account latch so a
# suspected wholesale-RESET alert (>= _ACCOUNT_RESET_SNAPSHOT_THRESHOLD
# strategy-attributed positions confirmed absent in one pass) fires ONCE, not
# every confirm window while the anomaly persists. Cleared when the account no
# longer presents a mass vanish (event resolved / rows manually reconciled).
# In-process (a restart re-arms from scratch — fail-safe toward re-alerting,
# never toward a silent mass-close).
_RESET_ALERT_LATCHED: set = set()

# 2-observation confirm cache for the STUCK-STRATEGY WATCHDOG's "position flat
# at exchange → finalize closed" branch (BL-20260708-WATCHDOG-FALSEFLAT-FLAP).
# The watchdog read ``account_open_positions`` ONCE and finalized the DB row
# closed on that single flat read — but a transient/partial Alpaca snapshot that
# momentarily omits a symbol is a FALSE flat: the position is still live. That
# false-close (with a fabricated local-compute PnL) then flapped via the reverse
# reconciler re-adopting the still-live exchange position as a brand-new
# ``adopted_orphan`` (the alpaca_paper QQQ #3249 → #3269 flap, ~17h open, naked).
# So the flat-finalize now requires the SAME 2-observation confirm the reverse
# reconciler already uses (``_close_confirm_seconds`` apart), keyed by trade id;
# the stamp is cleared the instant the position reads alive again (a flap resets
# the counter). In-process (a restart re-arms from scratch — fail-safe, never
# finalizes early).
_PENDING_WATCHDOG_FLAT_CONFIRM: Dict[int, datetime] = {}

# Per-tick set of ``(account_id, symbol)`` the monitor ATTEMPTED an active close
# on this tick (BL-20260708-ALPACA-REARM-VS-CLOSE-FIGHT). Cleared at the top of
# every ``run_monitor_tick``, populated in ``_apply_update`` just before the
# exchange close. The Alpaca broker-naked re-arm
# (``_check_broker_naked_equity_positions``) runs LATER in the same tick and
# skips any symbol in this set: without it the two subsystems FIGHT — the monitor
# cancels the resting OCO to close, its DELETE /v2/positions races the async
# cancel and fails ``insufficient qty available (available: 0)``, then the
# re-arm re-places the OCO before the next tick, so the shares stay perpetually
# ``held_for_orders``, the OCO stop never survives long enough to trigger, and
# the close never completes (the alpaca_paper QQQ #3269 perpetual close-failure).
# Suppressing the re-arm while a close is in flight lets the cancel settle and the
# market close flatten the position. In-process, per-tick (a restart / next tick
# rebuilds it — fail-safe: a genuinely naked position with no active close is
# still re-armed as before).
# DECOUPLE PREREQUISITE (2026-08-12) — this was a per-TICK set and the exit half
# WROTE it while the reconciler half READ it, which is only sound while both run
# inside one `run_monitor_tick` call. Moving the exit loop onto its own cadence
# breaks that silently: the clear-at-top either wipes the set before a reconciler
# reads it, or the reconciler reads a set built at an unrelated moment. Neither
# fails loudly — the re-arm just quietly resumes fighting an in-flight close,
# which is BL-20260708-ALPACA-REARM-VS-CLOSE-FIGHT returning by the back door.
#
# So the membership test becomes TIME-bounded rather than tick-bounded: a key is
# "actively closing" if a close was attempted within `_ACTIVE_CLOSE_WINDOW_S`.
# Lock-guarded because the two halves are two threads once the loop lands.
#
# TRADEOFF, stated rather than buried: the window is deliberately SHORT. Longer is
# safer against the re-arm fight but strictly worse for a close that fails
# PERMANENTLY — that position stays un-re-armed for the whole window, and an
# un-re-armed naked position is the condition the sweep exists to correct. 90s
# covers an in-flight close (IB_CLOSE_CONFIRM_S is 6s; Alpaca's
# cancel-then-flatten is slower) while letting a dead close resume re-arming
# within ~2 passes.
_ACTIVE_CLOSE_WINDOW_S_DEFAULT = 90.0
_ACTIVE_CLOSE_LOCK = threading.Lock()
_TICK_ACTIVE_CLOSE_AT: Dict[tuple, float] = {}


def _active_close_window_s() -> float:
    """Seconds a close-attempt marker suppresses the protective re-arm.

    Read at call time. An unparseable or non-positive value falls back to the
    default rather than disabling suppression — a typo must not silently
    re-enable the re-arm fight (the `EXPOSURE_SOAK_SECONDS` discipline).
    """
    try:
        v = float(os.environ.get("ACTIVE_CLOSE_WINDOW_S", "") or _ACTIVE_CLOSE_WINDOW_S_DEFAULT)
    except (TypeError, ValueError):
        return _ACTIVE_CLOSE_WINDOW_S_DEFAULT
    return v if v > 0 else _ACTIVE_CLOSE_WINDOW_S_DEFAULT


def mark_active_close(account_id: str, symbol: str) -> None:
    """Record that a close is in flight for (account, symbol)."""
    key = (str(account_id or ""), str(symbol or "").upper())
    with _ACTIVE_CLOSE_LOCK:
        _TICK_ACTIVE_CLOSE_AT[key] = time.monotonic()


def is_active_close(account_id: str, symbol: str) -> bool:
    """True when a close was attempted for this key inside the window.

    Also prunes expired keys, so the map cannot grow with uptime — it is bounded
    by the number of distinct (account, symbol) pairs closed in one window.
    """
    key = (str(account_id or ""), str(symbol or "").upper())
    cutoff = time.monotonic() - _active_close_window_s()
    with _ACTIVE_CLOSE_LOCK:
        for k in [k for k, ts in _TICK_ACTIVE_CLOSE_AT.items() if ts < cutoff]:
            _TICK_ACTIVE_CLOSE_AT.pop(k, None)
        return key in _TICK_ACTIVE_CLOSE_AT

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


def _close_confirm_seconds() -> float:
    """Min seconds a filled trade must read net-flat (across at least two
    observations) before the netting guard lets the reconciler close it.

    Read ``RECONCILER_CLOSE_CONFIRM_SECONDS`` at call time; falls back to
    ``_DEFAULT_CLOSE_CONFIRM_SECONDS`` on missing / unparseable values,
    clamped to ``>= 0``. The netting guard is now BASELINE (unconditional —
    the ``POSITION_NETTING_GUARD_ENABLED`` gate was removed 2026-06-17), so
    this confirm window applies on every tick. ``0`` keeps the
    extra-grace-tick requirement (a second confirming observation) but with
    no extra time wait.
    """
    raw = os.environ.get("RECONCILER_CLOSE_CONFIRM_SECONDS")
    if raw is None or str(raw).strip() == "":
        return float(_DEFAULT_CLOSE_CONFIRM_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_CLOSE_CONFIRM_SECONDS)


def _close_fail_alert_after() -> int:
    """Consecutive monitor-close failures before the operator is alerted.

    Reads ``MONITOR_CLOSE_FAIL_ALERT_AFTER`` at call time; falls back to
    ``_DEFAULT_CLOSE_FAIL_ALERT_AFTER`` on missing / unparseable values, clamped
    ``>= 1`` (item #3 — surface a position that won't close instead of retrying
    it silently forever).
    """
    # The close-fail alert is always on (default 3); this knob only tunes after
    # how many consecutive failures it fires — it strands no capability, so it is
    # not the BUG-039 default-off-gate class the env-gate purge forbids.
    raw = os.environ.get("MONITOR_CLOSE_FAIL_ALERT_AFTER")  # allow-silent: tuning knob (alert cadence), not a capability gate
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_CLOSE_FAIL_ALERT_AFTER
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_CLOSE_FAIL_ALERT_AFTER


def _env_float_clamped(name: str, default: float, floor: float) -> float:
    """Read *name* as a float at call time; fall back to *default*, clamp >= *floor*."""
    raw = os.environ.get(name)  # allow-silent: tuning knob (alert cadence), not a capability gate
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return max(floor, float(raw))
    except (TypeError, ValueError):
        return default


def _clear_close_fail_alert_state(key: tuple) -> None:
    """Drop the streak AND the paging-backoff state for *key*.

    Called wherever a close stops failing (a confirmed close, or a market-session
    DEFER). Keeping these three in one function is deliberate: the streak and the
    backoff must be cleared TOGETHER or the next genuine failure inherits a
    widened interval and pages late.
    """
    _CLOSE_FAIL_STREAK.pop(key, None)
    _CLOSE_FAIL_ALERT_AT.pop(key, None)
    _CLOSE_FAIL_ALERT_COUNT.pop(key, None)


def _should_alert_close_failure(key: tuple, streak: int, now: float | None = None) -> bool:
    """Whether this consecutive-close-failure should page the operator.

    First page once the streak reaches ``MONITOR_CLOSE_FAIL_ALERT_AFTER``; after
    that, page only once the interval since the last page has elapsed, doubling
    that interval each time up to the max. Never returns False forever — a
    permanently wedged position still pages at the max interval.
    """
    if streak < _close_fail_alert_after():
        return False
    now = time.monotonic() if now is None else now
    last = _CLOSE_FAIL_ALERT_AT.get(key)
    if last is None:
        _CLOSE_FAIL_ALERT_AT[key] = now
        _CLOSE_FAIL_ALERT_COUNT[key] = 1
        return True
    base = _env_float_clamped(
        "MONITOR_CLOSE_FAIL_ALERT_BACKOFF_S",
        _DEFAULT_CLOSE_FAIL_ALERT_BACKOFF_S, 0.0)
    cap = _env_float_clamped(
        "MONITOR_CLOSE_FAIL_ALERT_MAX_BACKOFF_S",
        _DEFAULT_CLOSE_FAIL_ALERT_MAX_BACKOFF_S, 0.0)
    sent = _CLOSE_FAIL_ALERT_COUNT.get(key, 1)
    # 2nd page after `base`, 3rd after 2*base, 4th after 4*base ... clamped.
    interval = min(base * (2 ** max(0, sent - 1)), max(base, cap))
    if now - last < interval:
        return False
    _CLOSE_FAIL_ALERT_AT[key] = now
    _CLOSE_FAIL_ALERT_COUNT[key] = sent + 1
    return True


def _close_retry_cooldown_seconds() -> float:
    """Seconds to defer re-attempting an active close after one was accepted but
    never confirmed flat — the BL-20260624-MHG-CLOSE-CONFIRM follow-up churn guard.

    Reads ``IB_CLOSE_RETRY_COOLDOWN_S`` at call time; falls back to
    ``_DEFAULT_CLOSE_RETRY_COOLDOWN_SECONDS`` on missing / unparseable values,
    clamped ``>= 0``. ``0`` disables the cooldown (retry the close every tick —
    the legacy behaviour, which churns the protective bracket when the venue
    can't fill).
    """
    raw = os.environ.get("IB_CLOSE_RETRY_COOLDOWN_S")
    if raw is None or str(raw).strip() == "":
        return float(_DEFAULT_CLOSE_RETRY_COOLDOWN_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_CLOSE_RETRY_COOLDOWN_SECONDS)


def _reconciler_readopt_guard_seconds() -> float:
    """Window (seconds) within which a just-closed ``adopted_orphan`` must NOT
    be re-adopted — the BL-20260618-RECONCILE-DUP flap guard.

    Reads ``RECONCILER_READOPT_GUARD_SECONDS`` at call time; falls back to
    ``_DEFAULT_READOPT_GUARD_SECONDS`` on missing / unparseable values, clamped
    ``>= 0``. ``0`` disables the guard (legacy re-adopt-immediately behaviour).
    """
    raw = os.environ.get("RECONCILER_READOPT_GUARD_SECONDS")
    if raw is None or str(raw).strip() == "":
        return float(_DEFAULT_READOPT_GUARD_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_READOPT_GUARD_SECONDS)


def _snapshot_min_fill_age_seconds() -> float:
    """Min age (seconds) a strategy-attributed trade must reach before the P3b
    position-snapshot reconciler may close it as ``exchange_flat_reconciled``.

    Reads ``RECONCILER_SNAPSHOT_MIN_FILL_AGE_S`` at call time; falls back to
    ``_DEFAULT_SNAPSHOT_MIN_FILL_AGE_SECONDS`` on missing / unparseable values,
    clamped ``>= 0``. Protects a freshly-placed order on a non-Bybit integration
    (alpaca/oanda) whose fill hasn't propagated to the open-positions snapshot
    yet from being false-closed (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE). ``0``
    disables the age gate (legacy behaviour — the 2-observation confirm alone).
    """
    raw = os.environ.get("RECONCILER_SNAPSHOT_MIN_FILL_AGE_S")
    if raw is None or str(raw).strip() == "":
        return float(_DEFAULT_SNAPSHOT_MIN_FILL_AGE_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_SNAPSHOT_MIN_FILL_AGE_SECONDS)


def _recently_closed_adopted_orphan(
    db, *, account_id: str, symbol: str, direction: str, window_seconds: float,
) -> Optional[dict]:
    """Return the most-recently-closed trade for this ``(account_id, symbol,
    direction)`` whose ``closed_at`` is within ``window_seconds`` of now AND
    whose close was either an adopted-orphan row OR a reconciler-initiated
    "we THINK this is flat" close on a non-Bybit integration — else ``None``.

    Backs the re-adopt flap guard (BL-20260618-RECONCILE-DUP): a position that
    matches a recently-closed row is a flap, not a new position — re-adopting
    it loops and books phantom losses. Originally matched only
    ``setup_type='adopted_orphan'`` (the bare ``orphan_adopt`` rows and the
    strategy-reattached adopted orphans). BL-20260707-ALPACA-CLOSE-NOT-
    CONFIRMED-FLAT widened this: a STRATEGY-ATTRIBUTED row closed by
    ``position_snapshot_reconciler`` (``exit_reason='exchange_flat_
    reconciled'``) or ``exit_coverage_resolver`` (``exit_reason=
    'exit_coverage_no_strategy'``) carries the ORIGINAL strategy's
    ``setup_type``, never ``'adopted_orphan'`` — so it fell straight through
    the old guard with ZERO flap protection. Both closers can be wrong on a
    non-Bybit integration with no per-order status reader (the position
    genuinely never left the broker, just went "accept-not-confirmed-flat" —
    the live SLV incident); a fresh false-close should not re-adopt
    immediately just because it didn't happen to carry the orphan setup_type.
    Deliberately NOT widened to every close reason — a real, broker-confirmed
    close (e.g. Bybit's ``reconciler_filled``, a normal ``sl_cross``/
    ``tp_cross`` strategy exit) is a genuine flatten and must never suppress a
    legitimate new position on the same symbol/direction. Best-effort: any DB
    error returns ``None`` (fail-open — never blocks a genuine adoption).
    """
    if window_seconds <= 0:
        return None
    canonical = {"buy": "long", "long": "long",
                 "sell": "short", "short": "short"}.get(str(direction or "").lower())
    if not symbol or not canonical:
        return None
    try:
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, symbol, direction, closed_at, pnl, order_package_id "
                "FROM trades "
                "WHERE status='closed' AND COALESCE(is_backtest,0)=0 "
                "  AND account_id=? AND symbol=? "
                "  AND (setup_type='adopted_orphan' "
                "       OR exit_reason IN "
                "           ('exchange_flat_reconciled', 'exit_coverage_no_strategy')) "
                "  AND closed_at IS NOT NULL "
                "ORDER BY closed_at DESC LIMIT 8",
                (account_id, symbol),
            ).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - fail-open: never block a real adopt on a read error
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    for r in rows:
        rdir = {"buy": "long", "long": "long",
                "sell": "short", "short": "short"}.get(str(r["direction"] or "").lower())
        if rdir != canonical:
            continue
        closed_dt = _parse_created_at(r["closed_at"])
        if closed_dt is not None and closed_dt >= cutoff:
            return {k: r[k] for k in r.keys()}
    return None


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


def _isoformat_to_ms(value: Any) -> Optional[int]:
    """Return *value* as epoch milliseconds, or ``None`` when it
    can't be parsed. Builds on :func:`_parse_created_at` for
    consistency with the rest of the reconciler.

    Used by the closed-pnl recovery path
    (``_close_trade_from_order_status``) to feed
    ``account_closed_pnl_for_trade``'s ``opened_at_ms`` parameter
    from the trade row's ``created_at`` column.
    """
    dt = _parse_created_at(value)
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _safe_float(value: Any) -> Optional[float]:
    """Best-effort coerce to float. ``None`` on failure or NaN.

    The qty filter on closed-pnl lookups uses this to forgive
    blank ``position_size`` values without raising.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f


def _load_account_cfgs_for_reconcile() -> Dict[str, Dict[str, Any]]:
    """Return ``{account_id: account_cfg_dict}`` from accounts.yaml.

    Account dicts carry the keys ``account_open_positions`` reads:
    ``account_id``, ``exchange``, ``api_key_env``, ``api_secret_env``,
    ``mode``, ``market_type``, ``demo``, plus the IB connection fields
    (``ib_host`` / ``ib_port`` / ``ib_account`` / ``ib_client_id``).
    The IB fields are load-bearing for ``ib_paper`` — without them
    ``ib_read_client_for(account)`` short-circuits at "ib_port unset"
    and the reconciler silently skips every IB account (spamming
    ``ib_client_for(ib_paper): no ib_port set`` on each monitor tick).
    Mirrors the dict shape ``Coordinator.multi_account_execute`` builds
    at ``coordinator.py``'s ``account_cfg`` so the two layers stay in
    lockstep. Best-effort — any read failure returns an empty dict so
    the reconciler runs as a no-op rather than orphaning trades on a
    config-load error.
    """
    from src.config.accounts_loader import load_accounts_dict
    raw = load_accounts_dict()
    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in raw.items():
        if cfg.get("enabled") is False:
            continue
        out[str(name)] = {
            "account_id": str(name),
            "exchange": cfg.get("exchange", "bybit"),
            "api_key_env": cfg.get("api_key_env"),
            "api_secret_env": cfg.get("api_secret_env"),
            "mode": cfg.get("mode") or "live",
            "market_type": cfg.get("market_type") or "spot",
            "demo": cfg.get("demo", False),
            # IB connection fields — without these the IB branch of
            # account_open_positions hits "ib_port unset" and returns
            # None, silently skipping every reconciler pass on MES.
            "ib_host": cfg.get("ib_host"),
            "ib_port": cfg.get("ib_port"),
            "ib_account": cfg.get("ib_account"),
            "ib_client_id": cfg.get("ib_client_id"),
            # Alpaca/OANDA host selector (paper vs live) + optional base_url.
            # Without these, account_open_positions' alpaca/oanda branch builds
            # the client against the PAPER/practice host, so a LIVE account's
            # live key 401s ("request is not authorized") and the reconciler
            # can never read its positions (BL-20260628-ALPACA-LIVE-HOST — the
            # 4th account-dict builder; the other three were fixed in #4916).
            "alpaca_env": cfg.get("alpaca_env"),
            "base_url": cfg.get("base_url"),
            "oanda_env": cfg.get("oanda_env"),
        }
    return out


def _resolve_account_class(
    account_id: str,
    cfgs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Best-effort canonical paper/real_money class for *account_id*.

    Mirrors the single source of truth in
    ``src.units.accounts.execute`` (``account_class = str(
    account_cfg.get("account_class") or "real_money").strip().lower()``):
    coerce a missing/invalid value to ``"real_money"`` so a config typo
    never silently mis-stamps a row, and never raise — a lookup failure
    falls back to ``"real_money"`` so the reconciler (best-effort) is
    never crashed by a config-load error.

    Reads ``config/accounts.yaml`` through the canonical
    ``load_accounts_dict`` reader (the ``canonical-config-loaders`` CI
    guard forbids hand-rolled parsers). Pass *cfgs* to inject an
    already-loaded ``{account_id: cfg}`` map (e.g. in tests or to avoid
    re-reading the YAML).
    """
    try:
        if cfgs is None:
            from src.config.accounts_loader import load_accounts_dict
            cfgs = load_accounts_dict()
        cfg = cfgs.get(str(account_id)) or {}
        account_class = str(cfg.get("account_class") or "real_money").strip().lower()
        if account_class not in ("paper", "real_money"):
            account_class = "real_money"
        return account_class
    except Exception:  # noqa: BLE001 — best-effort; never crash the reconciler
        return "real_money"


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

    One of ``detect_only`` / ``adopt`` / ``close``. **Default is ``adopt``**
    (operator directive 2026-06-24): an orphan is a problem to RESOLVE, never a
    status to rest in. ``adopt`` inserts a trade row so the journal regains
    visibility and the reconciler/strategy drives it to a real close —
    ``detect_only`` only alerts and lets the live position sit untracked, which
    is exactly the resting state we forbid. Making ``adopt`` the CODE default
    (not just the systemd-unit value) means a dropped ``.env`` var can't silently
    regress to the resting behaviour — the same class of failure that the
    removed netting-guard env-gate caused on the 2026-06-14 migration.

    Unknown values fall back to ``adopt`` (not the resting ``detect_only``)
    rather than raising, so a typo in the unit file never crashes the trader
    AND never strands an orphan as alert-only; the audit log captures the
    rejected value.
    """
    raw = str(os.environ.get("ORPHAN_POSITION_POLICY", "adopt")).strip().lower()
    if raw in _VALID_ORPHAN_POLICIES:
        return raw
    logger.warning(
        "ORPHAN_POSITION_POLICY=%r is not one of %s — falling back to adopt",
        raw, sorted(_VALID_ORPHAN_POLICIES),
    )
    return "adopt"


def _alert_account_reset(account_id: str, symbols: List[str]) -> None:
    """One consolidated 'account reset SUSPECTED' alert (Telegram + WARNING FCM).

    Fired once (latched per account) when the position-snapshot reconciler finds
    >= ``_ACCOUNT_RESET_SNAPSHOT_THRESHOLD`` positions for one account confirmed
    absent in a single pass. RISK-1 (BL-20260707-RECONCILER-MASS-FALSE-CLOSE):
    the reconciler NO LONGER auto-closes on a mass vanish — mass-closing N live
    rows on one inference is the amplifier that turned a bad 2026-07-07 read into
    7 false closes. The rows are left OPEN; this alert asks the operator to
    resolve. Best-effort — never raises into the reconcile sweep.
    """
    uniq: List[str] = []
    for s in symbols:
        if s and s not in uniq:
            uniq.append(s)
    n = len(symbols)
    msg = (
        f"\U0001F501 [ALERT] Account RESET suspected: {account_id}\n"
        f"{n} open position(s) vanished from the exchange in one snapshot "
        f"({', '.join(uniq[:12])}{' …' if len(uniq) > 12 else ''}). A wholesale "
        "vanish is treated as an account-level anomaly (external reset/wipe / "
        "auth-scope change / broker incident), NOT strategy exits — so the DB "
        "rows are left OPEN, NOT auto-closed (mass-closing on one read is how a "
        "bad snapshot false-closed live positions on 2026-07-07). Please verify "
        "on the broker whether these are genuinely flat; if so, close them "
        "manually. If this recurs, check who/what has reset access to the account."
    )
    try:
        from src.runtime.notify import send_telegram_direct
        send_telegram_direct(msg, parse_mode=None, mirror_to_fcm=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_alert_account_reset: telegram send failed: %s", exc)
    try:
        from src.runtime.mobile_push import publish_event
        from src.runtime.mobile_push.event_kinds import WARNING
        publish_event(WARNING, {"text": msg})
    except Exception as exc:  # noqa: BLE001
        logger.debug("_alert_account_reset: fcm WARNING publish failed: %s", exc)


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

    Runs unconditionally every monitor tick (the MONITOR_RECONCILE_ENABLED
    gate was removed 2026-06-15, BL-20260615-MGCNAKED — self-heal is baseline
    correctness). Best-effort — every step is wrapped; one bad position never
    aborts the sweep.

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
        # Adopted-orphan rows that read absent this pass but are inside the
        # close-confirm window (armed or awaiting a second confirming read) —
        # NOT yet closed. A logged-out-Gateway empty-portfolio blip surfaces
        # here for one pass and clears when the position reads back open.
        # BL-20260614-ORPHANBLIP.
        "pending_disappear": 0,
        # Existing orphan_adopt rows repaired back to their originating
        # strategy this pass (self-heal — orphan_adopt is a problem state,
        # not a resting status).
        "reattached_existing": 0,
        # Un-attributable orphan_adopt rows flattened this pass because no
        # live order package could be associated (exit-coverage reattach-or-
        # close); and rows inside the close-confirm window awaiting a 2nd
        # observation before that flatten.
        "resolved_closed": 0,
        "resolved_pending_close": 0,
        # P3b universal position-snapshot reconciliation: strategy-attributed
        # DB-open rows on NON-order-status integrations (IB / Alpaca — not
        # Bybit, which the forward reconciler owns) confirmed absent from a
        # SUCCESSFUL exchange snapshot and closed (snapshot_closed), or absent
        # this pass but inside the 2-observation confirm window (snapshot_pending).
        "snapshot_closed": 0,
        # RISK-1 (BL-20260707-RECONCILER-MASS-FALSE-CLOSE): kept at 0 for
        # back-compat — a wholesale RESET is NO LONGER auto-closed. A mass vanish
        # now ALERTS (snapshot_reset_alerted) and leaves the rows OPEN. (Historical
        # exchange_reset_flat rows from before this change still exist and are
        # still excluded from strategy metrics by _clean_trades.)
        "snapshot_reset_closed": 0,
        # Strategy-attributed rows confirmed absent in a suspected wholesale
        # RESET (>= _ACCOUNT_RESET_SNAPSHOT_THRESHOLD in one pass): alerted +
        # left OPEN, never auto-closed on inference.
        "snapshot_reset_alerted": 0,
        # Rows absent from the batch LIST but NOT closed because a positive
        # per-symbol broker check (alpaca) said still-open (True) or could-not-
        # confirm (None) — only a broker-confirmed flat closes (RISK-1).
        "snapshot_presence_unconfirmed": 0,
        "snapshot_pending": 0,
        # Strategy-attributed rows absent from the snapshot but younger than the
        # fresh-fill grace (_snapshot_min_fill_age_seconds) — skipped, NOT armed
        # or closed, so a still-propagating fill isn't false-closed
        # (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE).
        "snapshot_too_young": 0,
        # Adoptions SUPPRESSED this pass because the position matches an
        # adopted_orphan that closed within the re-adopt guard window — a
        # gateway flap, not a new position (BL-20260618-RECONCILE-DUP). Stops
        # one exchange position being re-adopted N times into N phantom trades.
        "readopt_suppressed": 0,
        "errors": 0,
    }

    # Self-heal first: an `orphan_adopt` row is an unresolved problem, never a
    # legitimate resting status. Every existing one is RESOLVED each pass —
    # reattached to its originating strategy when the origin is recoverable (so
    # it regains active monitor()), else CLOSED, because a trade with no
    # rational exit strategy must be exited, not left resting on a static stop
    # (operator decision 2026-06-15, exit-coverage). Runs every pass;
    # idempotent; confident-match reattach + 2-observation-confirmed close.
    # Independent of ORPHAN_POSITION_POLICY (repair is always correct).
    try:
        _reattach_adopted_orphans(db, summary)
    except Exception as exc:  # noqa: BLE001 — repair must never break reconcile
        logger.warning("_reattach_adopted_orphans pass failed: %s", exc)
        summary["errors"] += 1

    policy = _orphan_position_policy()
    cfgs = _load_account_cfgs_for_reconcile()
    if not cfgs:
        return summary

    from src.units.accounts.clients import (
        account_open_positions,
        account_position_present,
        account_supports_management,
        supports_position_presence,
    )
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
                    "SELECT id, symbol, direction, strategy_name, account_id, "
                    "       position_size, entry_price, notes, order_package_id, "
                    "       timestamp, created_at "
                    "FROM trades "
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
        now_iso_dt = datetime.now(timezone.utc)
        now_iso = now_iso_dt.isoformat()
        for r in open_rows:
            if str(r["strategy_name"] or "") != "orphan_adopt":
                continue
            sym = r["symbol"]
            side = str(r["direction"] or "").lower()
            canonical = {"buy": "long", "long": "long",
                         "sell": "short", "short": "short"}.get(side)
            if not sym or not canonical:
                continue
            tid_int = int(r["id"])
            if (sym, canonical) in exchange_positions:
                # Still alive on the exchange — clear any disappear-confirm (a
                # prior absent read was a blip).
                _PENDING_ORPHAN_DISAPPEAR_CONFIRM.pop(tid_int, None)
                # Exit-coverage reattach-or-close: this row is still
                # orphan_adopt after the top-of-pass reattach, so it has no
                # recoverable strategy — and it IS alive on the exchange. A
                # position with no rational exit strategy is flattened
                # (2-observation confirmed). Re-check recoverability first
                # (cheap) so a row that just became reattachable is never closed.
                try:
                    if _recover_orphan_order_package(
                        db=db, symbol=sym, direction=r["direction"],
                        entry_price=float(r["entry_price"] or 0.0),
                    ) is None:
                        _close_unattributable_orphan(db, r, summary)
                    else:
                        _PENDING_ORPHAN_NOSTRAT_CLOSE.pop(tid_int, None)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "_reconcile_orphan_exchange_positions: exit-coverage "
                        "close failed for trade_id=%s: %s", tid_int, exc,
                    )
                    summary["errors"] += 1
                continue
            # Disappeared from this snapshot. Require a SECOND confirming
            # observation (>= _close_confirm_seconds apart) before closing, so
            # a logged-out-Gateway empty-portfolio blip can't close (and then
            # re-orphan) the adopted row. BL-20260614-ORPHANBLIP.
            _first_absent = _PENDING_ORPHAN_DISAPPEAR_CONFIRM.get(tid_int)
            if _first_absent is None:
                _PENDING_ORPHAN_DISAPPEAR_CONFIRM[tid_int] = now_iso_dt
                summary["pending_disappear"] += 1
                logger.info(
                    "_reconcile_orphan_exchange_positions: ARMED close-confirm "
                    "for disappeared adopted orphan — trade_id=%s account=%s "
                    "symbol=%s side=%s (awaiting a second confirming read)",
                    tid_int, aid, sym, canonical,
                )
                continue
            if (now_iso_dt - _first_absent).total_seconds() < _close_confirm_seconds():
                # Still inside the confirm window — wait for the next pass.
                summary["pending_disappear"] += 1
                continue
            _PENDING_ORPHAN_DISAPPEAR_CONFIRM.pop(tid_int, None)
            # package-cascade: KNOWN GAP — this path closes the trade and does
            # NOT close the linked `order_packages` row, so the second-line
            # `_sweep_stuck_linked_packages` picks it up a tick later and stamps
            # the generic close_reason='stuck_cascade_recovered' (a bookkeeping
            # repair, NOT an exit) while ALSO firing `enqueue_stuck_package_sweep`.
            # ⚠️ The intuitive exemption -- "an adopted orphan has no package" --
            # is FALSE and was MEASURED false 2026-08-22: 44 of 74
            # setup_type='adopted_orphan' rows carry a non-null order_package_id
            # (26 of them close with exactly this exit_reason). Fixing it is
            # Tier-2 (it advances the strategy-monocle gate's unblock by ~1 tick),
            # so it is filed rather than patched here:
            # BL-20260822-SEVEN-OF-EIGHT-TRADE-CLOSE-SITES-DO-NOT-CASCADE-THEIR-PACKAGE.
            try:
                db.update_trade(tid_int, {
                    "status": "closed",
                    "exit_reason": "adopted_orphan_disappeared",
                    "closed_at": now_iso,
                    "notes": dump_capped({
                        "closed_at": now_iso,
                        "closed_by": "reverse_reconciler",
                        "closed_reason": (
                            "exchange no longer reports the adopted position; "
                            "exchange-side SL/TP or manual close took it out"
                        ),
                    }, 500),
                })
                summary["closed_disappeared"] += 1
                # Sweep any resting protective legs now that the position is
                # confirmed flat — a reconciler flat-close never went through
                # IBClient.close, so stale IB OCA stops could otherwise fire and
                # flip a flat position into a reverse orphan (BL-20260624-MHG-FLIP).
                _cancel_resting_protection_after_flat(aid, sym)
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

        # ── P3b: universal position-snapshot reconciliation ──────────────
        # Close STRATEGY-ATTRIBUTED DB-open rows (NOT orphan_adopt — those are
        # handled above) on integrations WITHOUT a per-order status reader,
        # when their (symbol, side) is confirmed absent from this SUCCESSFUL
        # snapshot. Bybit declares the ``order_status`` management cap and is
        # reconciled by the forward reconciler (_reconcile_open_trades), so it
        # is skipped here — no double-handling. An integration that doesn't
        # support the ``open_positions`` snapshot can't be reconciled this way
        # and is left as-is (its rows stay open, exactly as before P3b).
        #
        # Safety (mirrors the orphan close-on-disappear path exactly):
        #   * ``positions`` is non-None here (the None read-failure short-
        #     circuit ``continue`` above already skipped this whole account),
        #     so we only ever act on a SUCCESSFUL snapshot.
        #   * a row reads absent → ARM (first observation), not close.
        #   * absent again, ≥ _close_confirm_seconds later → CLOSE.
        #   * present in the snapshot → CLEAR any pending arming (the prior
        #     absent read was a blip) and leave the row open.
        # PnL is left NULL: the merged local-PnL sweep fills it next tick
        # (mark-to-market). No exchange order is sent — the position is already
        # gone exchange-side; this is bookkeeping reconciliation.
        _supports_order_status = account_supports_management(cfg, "order_status")
        _supports_open_positions = account_supports_management(cfg, "open_positions")
        if not _supports_order_status and _supports_open_positions:
            # Collect the rows that reach "close now" (confirm-window elapsed +,
            # for alpaca, a positive per-symbol broker-confirmed flat) this pass,
            # then decide AFTER the loop whether this is a wholesale account RESET
            # (>= _ACCOUNT_RESET_SNAPSHOT_THRESHOLD positions vanishing at once —
            # the 2026-07-07 alpaca_paper paper-reset signature) vs individual
            # disappearances. RISK-1: a suspected reset is NOT auto-closed — it
            # fires ONE latched alert and leaves the rows OPEN; only individual
            # (< threshold) confirmed-flat disappearances auto-close.
            _snapshot_to_close: List[Tuple[Any, int, str, str]] = []
            # Track whether ANY strategy row for this account read absent from
            # the batch snapshot this pass (armed / pending / confirmed alike).
            # Used only to clear the reset-alert latch when the account is
            # HEALTHY again (every row present) — NOT on a mere arming pass,
            # which would defeat the latch.
            _any_absent = False
            for r in open_rows:
                # orphan_adopt rows are owned by the close-on-disappear pass
                # above; only reconcile genuine strategy-attributed trades.
                if str(r["strategy_name"] or "") == "orphan_adopt":
                    continue
                sym = r["symbol"]
                side = str(r["direction"] or "").lower()
                canonical = {"buy": "long", "long": "long",
                             "sell": "short", "short": "short"}.get(side)
                if not sym or not canonical:
                    continue
                tid_int = int(r["id"])
                if (sym, canonical) in exchange_positions:
                    # Still open on the exchange — clear any pending arming.
                    _PENDING_SNAPSHOT_DISAPPEAR_CONFIRM.pop(tid_int, None)
                    continue
                # Row is absent from the batch snapshot (armed/pending/confirmed
                # below) — the account is not fully healthy this pass.
                _any_absent = True
                # Fresh-fill grace (BL-20260622-ALPACA-SNAPSHOT-FALSECLOSE): a
                # just-placed order on an integration without a per-order status
                # reader (alpaca/oanda) can take minutes to fill AND propagate to
                # the open-positions endpoint. Until it does it reads absent here
                # yet is NOT flat — it's pending fill. Skip a strategy-attributed
                # trade younger than the grace so a propagating fill can't be
                # false-closed (and then re-adopted as an orphan). Don't even ARM
                # — a too-young row should leave no pending state. Fail-open: a
                # row whose age can't be parsed is treated as old enough (so a
                # genuinely-stale flat row is never stranded).
                _min_age = _snapshot_min_fill_age_seconds()
                if _min_age > 0:
                    _opened = (
                        _parse_created_at(r["timestamp"])
                        or _parse_created_at(r["created_at"])
                    )
                    _age = (
                        (now_iso_dt - _opened).total_seconds()
                        if _opened is not None else None
                    )
                    # Skip ONLY a positively-young row (0 <= age < grace).
                    # Fail-open: an unparseable age (None) or a non-positive age
                    # (future-dated row / clock skew) is treated as old enough so
                    # a genuinely-stale flat row is never stranded.
                    if _age is not None and 0.0 <= _age < _min_age:
                        summary["snapshot_too_young"] = (
                            summary.get("snapshot_too_young", 0) + 1
                        )
                        # Clear any arming from a pre-fill blip so the confirm
                        # window starts fresh once the grace elapses.
                        _PENDING_SNAPSHOT_DISAPPEAR_CONFIRM.pop(tid_int, None)
                        continue
                # Absent from a successful snapshot. Require a SECOND confirming
                # observation (>= _close_confirm_seconds apart) before closing.
                _first_absent = _PENDING_SNAPSHOT_DISAPPEAR_CONFIRM.get(tid_int)
                if _first_absent is None:
                    _PENDING_SNAPSHOT_DISAPPEAR_CONFIRM[tid_int] = now_iso_dt
                    summary["snapshot_pending"] += 1
                    logger.info(
                        "_reconcile_orphan_exchange_positions: ARMED snapshot "
                        "close-confirm — trade_id=%s account=%s symbol=%s side=%s "
                        "(strategy=%s; absent from exchange snapshot, awaiting a "
                        "second confirming read)",
                        tid_int, aid, sym, canonical, r["strategy_name"],
                    )
                    continue
                if (now_iso_dt - _first_absent).total_seconds() < _close_confirm_seconds():
                    # Still inside the confirm window — wait for the next pass.
                    summary["snapshot_pending"] += 1
                    continue
                _PENDING_SNAPSHOT_DISAPPEAR_CONFIRM.pop(tid_int, None)
                # RISK-1 (BL-20260707-ALPACA-PAPER-NEGATIVE-EQUITY /
                # -RECONCILER-MASS-FALSE-CLOSE): before closing on ABSENCE,
                # require a POSITIVE broker-confirmed flat where the integration
                # can give one (alpaca: GET /v2/positions/{symbol}). The batch
                # account_open_positions() LIST can be partial/stale — some rows
                # visible, this symbol merely omitted — and the empty-only balance
                # guard doesn't catch a PARTIAL read. Closing on that inference is
                # exactly what mass-false-closed 7 still-open alpaca_paper
                # positions on 2026-07-07 (then re-adopted them ~2h later with a
                # fabricated local-mark PnL). A direct per-symbol check
                # distinguishes genuinely-flat (404 → is False → close) from
                # "LIST was partial" (2xx → is True → keep open) and "couldn't
                # read" (None → skip this pass, never close on inference).
                # Integrations WITHOUT a per-symbol endpoint (IB / OANDA — read
                # the whole portfolio in one call) keep the 2-observation LIST
                # behaviour unchanged (supports_position_presence gate → no
                # regression, bounded blast radius).
                if supports_position_presence(cfg):
                    present = account_position_present(cfg, sym)
                    if present is not False:
                        summary["snapshot_presence_unconfirmed"] = (
                            summary.get("snapshot_presence_unconfirmed", 0) + 1
                        )
                        logger.warning(
                            "_reconcile_orphan_exchange_positions: NOT closing "
                            "trade_id=%s account=%s symbol=%s side=%s — absent "
                            "from the batch snapshot but per-symbol broker "
                            "confirm=%r (True=still open / None=unconfirmed; only "
                            "a broker-confirmed flat closes). RISK-1 guard against "
                            "a partial-LIST false-close.",
                            tid_int, aid, sym, canonical, present,
                        )
                        continue
                # Defer the close: collect it and decide reset-vs-single AFTER
                # the whole account's open_rows are scanned (below).
                _snapshot_to_close.append((r, tid_int, sym, canonical))

            # ── decide: wholesale account RESET vs individual disappearance ──
            # RISK-1 (BL-20260707-RECONCILER-MASS-FALSE-CLOSE): a mass vanish is
            # NEVER auto-closed. Even though each collected row here is already
            # per-symbol-confirmed flat (alpaca 404) or two-observation-confirmed
            # absent (IB/OANDA), >= _ACCOUNT_RESET_SNAPSHOT_THRESHOLD positions
            # disappearing from ONE account in a single pass is an account-level
            # anomaly (external wipe / auth-scope change / broker incident), NOT N
            # independent strategy exits — and mass-closing N live rows with a
            # fabricated local-mark PnL is the exact amplifier that turned one bad
            # 2026-07-07 read into 7 false closes + re-adoptions. So a suspected
            # reset ALERTS (once, latched) and leaves the rows OPEN for an operator
            # to resolve; only an INDIVIDUAL (< threshold) confirmed-flat
            # disappearance auto-closes.
            _is_reset = (
                len(_snapshot_to_close) >= _ACCOUNT_RESET_SNAPSHOT_THRESHOLD
            )
            if _is_reset:
                _vanished_syms = [str(s) for (_r, _t, s, _c) in _snapshot_to_close]
                summary["snapshot_reset_alerted"] = (
                    summary.get("snapshot_reset_alerted", 0)
                    + len(_snapshot_to_close)
                )
                logger.warning(
                    "_reconcile_orphan_exchange_positions: RESET-SUSPECT for "
                    "account=%s — %d strategy-attributed positions confirmed "
                    "absent in ONE pass (%s). NOT auto-closing (RISK-1: never "
                    "mass-close on inference); leaving the rows OPEN for manual "
                    "resolution and alerting the operator.",
                    aid, len(_snapshot_to_close), ", ".join(_vanished_syms),
                )
                # Latch so the alert fires ONCE per account per episode, not on
                # every confirm window while the anomaly persists.
                if aid not in _RESET_ALERT_LATCHED:
                    _RESET_ALERT_LATCHED.add(aid)
                    try:
                        _alert_account_reset(aid, _vanished_syms)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "_reconcile_orphan_exchange_positions: reset-alert "
                            "enqueue failed for account=%s: %s", aid, exc,
                        )
            else:
                _reset_note = (
                    "(symbol, side) confirmed absent from the exchange "
                    "open-positions snapshot across two observations AND, where "
                    "the integration supports it, by a direct per-symbol broker "
                    "check (alpaca 404); integration has no per-order status "
                    "reader (non-Bybit). PnL filled by the local-PnL sweep "
                    "(mark-to-market)."
                )
                for (r, tid_int, sym, canonical) in _snapshot_to_close:
                    try:
                        db.update_trade(tid_int, {
                            "status": "closed",
                            "exit_reason": "exchange_flat_reconciled",
                            "closed_at": now_iso,
                            "notes": dump_capped({
                                "closed_at": now_iso,
                                "closed_by": "position_snapshot_reconciler",
                                "reset_event": False,
                                "closed_reason": _reset_note,
                            }, 500),
                        })
                        # Cascade-close the linked order package, like every other
                        # reconciler close path.
                        _cascade_close_linked_package(
                            db, tid_int,
                            close_reason="exchange_flat_reconciled",
                            caller="_reconcile_orphan_exchange_positions(snapshot)",
                        )
                        summary["snapshot_closed"] += 1
                        # Sweep resting protective legs now the position is
                        # confirmed flat (this snapshot reconcile never went
                        # through IBClient.close) so a stale IB OCA stop can't fire
                        # and flip the flat position into a reverse orphan
                        # (BL-20260624-MHG-FLIP).
                        _cancel_resting_protection_after_flat(aid, sym)
                        logger.warning(
                            "_reconcile_orphan_exchange_positions: CLOSED via "
                            "position-snapshot reconcile — trade_id=%s account=%s "
                            "symbol=%s side=%s strategy=%s (confirmed absent from "
                            "exchange snapshot + per-symbol check where available; "
                            "PnL via local sweep)",
                            tid_int, aid, sym, canonical, r["strategy_name"],
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "_reconcile_orphan_exchange_positions: snapshot close "
                            "failed for trade_id=%s account=%s symbol=%s: %s",
                            r.get("id"), aid, sym, exc,
                        )
                        summary["errors"] += 1

            # Clear the reset-alert latch only when the account is HEALTHY again
            # (every strategy row present in the snapshot this pass), so a future
            # genuine reset re-alerts — but an arming/pending pass (rows still
            # absent, not yet confirmed) does NOT reset the latch and cause a
            # duplicate alert.
            if not _any_absent:
                _RESET_ALERT_LATCHED.discard(aid)

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
                # Re-adopt flap guard (BL-20260618-RECONCILE-DUP): if this
                # (account, symbol, side) matches an adopted_orphan that closed
                # within the guard window, the exchange position is flapping
                # (gateway logout → empty → back, or a DB-only sl_cross close on
                # a position the broker never actually closed) — re-adopting it
                # loops and books phantom losses. Suppress + alert; the real
                # exchange position is still operator-/SL-protected.
                _recent_closed = _recently_closed_adopted_orphan(
                    db,
                    account_id=aid,
                    symbol=str(sym),
                    direction=canonical_side,
                    window_seconds=_reconciler_readopt_guard_seconds(),
                )
                if _recent_closed is not None:
                    summary["readopt_suppressed"] += 1
                    note = (
                        "re-adopt suppressed: adopted_orphan trade_id="
                        f"{_recent_closed.get('id')} for {sym}/{canonical_side} "
                        f"on {aid} closed within the re-adopt guard window "
                        "(BL-20260618-RECONCILE-DUP flap guard) — not re-adopting"
                    )
                    logger.warning(
                        "_reconcile_orphan_exchange_positions: RE-ADOPT "
                        "SUPPRESSED — account=%s symbol=%s side=%s matches "
                        "recently-closed adopted_orphan trade_id=%s (flap "
                        "guard); enqueueing alert instead of re-adopting",
                        aid, sym, canonical_side, _recent_closed.get("id"),
                    )
                    try:
                        enqueue_exchange_orphan_adoption(
                            account=aid,
                            symbol=str(sym),
                            side=canonical_side,
                            size=size,
                            entry_price=entry_price,
                            db_trade_id=None,
                            policy="detect_only",
                            note=note,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "_reconcile_orphan_exchange_positions: alert enqueue "
                            "failed (readopt-suppressed) account=%s symbol=%s: %s",
                            aid, sym, exc,
                        )
                    continue
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
                    # Operator directive (2026-06-24): a NEW orphan row is a
                    # red flag, never an acceptable resting status. Durably log
                    # it for the health-review backlog drain AND fire a loud
                    # "initiate a /system-review" Telegram ping. Best-effort —
                    # a notify failure must never abort the reconcile sweep.
                    try:
                        from src.runtime.execution_diagnostics import (
                            enqueue_orphan_created_flag,
                        )
                        enqueue_orphan_created_flag(
                            account=aid, symbol=str(sym), side=canonical_side,
                            trade_id=db_trade_id, origin="reverse_reconciler_adopt",
                            reason=("exchange position had no matching open "
                                    "journal row — adopted as orphan"),
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "_reconcile_orphan_exchange_positions: orphan-created "
                            "flag failed for trade_id=%s: %s", db_trade_id, exc,
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
        or summary["pending_disappear"]
        or summary["snapshot_closed"]
        or summary["snapshot_pending"]
        or summary["readopt_suppressed"]
        or summary["errors"]
    ):
        logger.info(
            "_reconcile_orphan_exchange_positions: accounts=%d positions=%d "
            "orphans=%d adopted=%d closed=%d closed_disappeared=%d "
            "pending_disappear=%d snapshot_closed=%d snapshot_pending=%d "
            "readopt_suppressed=%d detect_only=%d errors=%d",
            summary["checked_accounts"], summary["checked_positions"],
            summary["orphans_found"], summary["adopted"], summary["closed"],
            summary["closed_disappeared"], summary["pending_disappear"],
            summary["snapshot_closed"], summary["snapshot_pending"],
            summary["readopt_suppressed"], summary["detect_only"], summary["errors"],
        )
    return summary


def _canon_dir(direction: Any) -> Optional[str]:
    """Normalise buy/long → 'long', sell/short → 'short' (else None)."""
    d = str(direction or "").lower()
    if d in ("buy", "long"):
        return "long"
    if d in ("sell", "short"):
        return "short"
    return None


def _recover_orphan_order_package(
    *, db, symbol: str, direction: str, entry_price: float,
    max_rel_diff: float = 0.02, limit: int = 30,
) -> Optional[dict]:
    """Best-effort find the order package that originally opened an exchange
    orphan, so the position can be returned to its strategy's monitoring.

    Matches newest-first on ``symbol`` + normalised ``direction``, requiring
    the package ``entry`` within ``max_rel_diff`` (relative) of the exchange
    entry to count as a confident match — so we never mis-attribute a
    position to the wrong strategy (which would apply the wrong exit rules).
    Returns the package dict, or ``None`` when no confident match exists
    (caller then falls back to a bare ``orphan_adopt`` row).
    """
    want = _canon_dir(direction)
    if not want or not entry_price:
        return None
    try:
        candidates = db.get_recent_order_packages_for_symbol(symbol, limit=limit)
    except Exception:  # noqa: BLE001 — best-effort; fall back to orphan_adopt
        return None
    for c in candidates:
        if _canon_dir(c.get("direction")) != want:
            continue
        pe = c.get("entry")
        if pe is None:
            continue
        try:
            if abs(float(pe) - entry_price) / entry_price <= max_rel_diff:
                return c
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return None


def _reattach_adopted_orphans(db, summary: Dict[str, int]) -> None:
    """Reattach every still-open ``orphan_adopt`` trade row whose originating
    order package is recoverable (the first half of exit-coverage
    reattach-or-close).

    ``orphan_adopt`` is a problem indicator, not a legitimate resting status —
    a position with no strategy attribution runs on static SL/TP with no active
    ``monitor()``. This drives each recoverable one back to its real strategy on
    every reconcile pass (confident symbol + normalised direction +
    entry-within-tolerance match): restore the package's SL/TP, re-arm the
    broker bracket, reopen + re-link the package so ``run_monitor_tick`` governs
    it again. Idempotent; best-effort per row.

    An **un-recoverable** orphan is left for the caller's per-account pass, which
    has the exchange snapshot: a still-alive one is FLATTENED
    (:func:`_close_unattributable_orphan` — a trade with no rational exit
    strategy is exited, not rested on a static stop, operator decision
    2026-06-15), a disappeared one is closed by the close-on-disappear pass.
    """
    import sqlite3 as _sqlite3

    conn = db.connect()
    try:
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT id, symbol, direction, entry_price, account_id, "
            "       position_size, notes, order_package_id FROM trades "
            "WHERE status='open' AND COALESCE(is_backtest,0)=0 "
            "  AND strategy_name='orphan_adopt'",
        ).fetchall()
    finally:
        conn.close()

    for r in rows:
        try:
            entry_price = float(r["entry_price"] or 0.0)
        except (TypeError, ValueError):
            continue
        recovered = _recover_orphan_order_package(
            db=db, symbol=r["symbol"], direction=r["direction"],
            entry_price=entry_price,
        )
        if recovered is None:
            # Un-recoverable here. The flatten decision is made in the
            # per-account loop below, where the position's exchange-aliveness is
            # known (still-alive → flatten; disappeared → close-on-disappear).
            continue
        # Recoverable → reattach; clear any pending exit-coverage close.
        _PENDING_ORPHAN_NOSTRAT_CLOSE.pop(int(r["id"]), None)
        opid = recovered.get("order_package_id")
        strat = recovered.get("strategy_name")
        try:
            db.update_trade(int(r["id"]), {
                "strategy_name": strat,
                "stop_loss": recovered.get("sl"),
                "take_profit_1": recovered.get("tp"),
                "entry_reason": "reverse_reconciler_reattached_existing_orphan",
                # Now tied back to its real strategy + order package (item #4).
                "reconcile_status": "reconciled",
            })
            db.update_order_package(opid, {
                "status": "open",
                "linked_trade_id": int(r["id"]),
                "close_reason": None,
            })
        except Exception as exc:  # noqa: BLE001 — best-effort per row
            logger.warning(
                "_reattach_adopted_orphans: re-attach of trade %s failed: %s",
                r["id"], exc,
            )
            continue
        # Re-arm the broker-side stop. The journal SL/TP above is only the
        # dashboard view — the exchange position is still naked until we place
        # a protective bracket. Unconditional: part of healing the orphan.
        try:
            _rearm_broker_protection_after_recovery(
                db, int(r["id"]), recovered.get("sl"), recovered.get("tp"),
            )
        except Exception as exc:  # noqa: BLE001 — best-effort; never abort the pass
            logger.warning(
                "_reattach_adopted_orphans: broker re-arm failed for trade "
                "%s: %s", r["id"], exc,
            )
        summary["reattached_existing"] = summary.get("reattached_existing", 0) + 1
        logger.warning(
            "_reattach_adopted_orphans: RE-ATTACHED existing orphan trade %s "
            "(%s/%s) to strategy %s via package %s — now under monitoring",
            r["id"], r["symbol"], r["direction"], strat, opid,
        )


def _close_unattributable_orphan(db, row, summary: Dict[str, int]) -> None:
    """Flatten an open ``orphan_adopt`` trade that has NO recoverable order
    package — it has no rational exit strategy, so it is exited rather than
    left resting on a static stop (exit-coverage reattach-or-close).

    A 2-observation confirm (``_PENDING_ORPHAN_NOSTRAT_CLOSE`` +
    ``_close_confirm_seconds``) guards against closing on a transient read where
    the originating package simply hasn't been written yet. The exchange flatten
    reuses the monitor's reduce-only close path (``_send_close_to_exchange``,
    which short-circuits dry-run); on a failed close the row is left open and
    retried next tick. Best-effort; never raises.
    """
    tid = int(row["id"])
    now = datetime.now(timezone.utc)

    first = _PENDING_ORPHAN_NOSTRAT_CLOSE.get(tid)
    if first is None:
        _PENDING_ORPHAN_NOSTRAT_CLOSE[tid] = now
        summary["resolved_pending_close"] = summary.get("resolved_pending_close", 0) + 1
        logger.warning(
            "_reattach_adopted_orphans: orphan trade %s (%s/%s) has no "
            "recoverable strategy — pending close (awaiting 2nd observation)",
            tid, row["symbol"], row["direction"],
        )
        return
    if (now - first).total_seconds() < _close_confirm_seconds():
        summary["resolved_pending_close"] = summary.get("resolved_pending_close", 0) + 1
        return

    # Confirmed un-attributable across >= 2 observations → flatten.
    try:
        qty = float(row["position_size"] or 0.0)
    except (TypeError, ValueError):
        qty = 0.0

    if qty > 0:
        resp = _send_close_to_exchange({
            "account_id": row["account_id"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "position_size": qty,
        })
        if not (resp or {}).get("ok"):
            summary["errors"] = summary.get("errors", 0) + 1
            logger.warning(
                "_reattach_adopted_orphans: exchange close FAILED for "
                "un-attributable orphan trade %s: %r — retrying next tick",
                tid, (resp or {}).get("error"),
            )
            return  # keep the pending entry; retry the close next tick
        skipped = (resp or {}).get("skipped")
    else:
        # Zero/!known size — nothing to flatten on the exchange; just record
        # the journal close so the row stops surfacing as an open orphan.
        skipped = "zero_size"

    now_iso = now.isoformat()
    notes = _decode_notes(row["notes"]) if _row_has(row, "notes") else {}
    notes.update({
        "closed_at": now_iso,
        "closed_by": "exit_coverage_resolver",
        "closed_reason": (
            "no recoverable strategy / order package — flattened "
            "(exit-coverage reattach-or-close)"
        ),
        "exchange_close_skipped": skipped,
    })
    # package-cascade: HANDLED, not missing. This path closes the linked
    # `order_packages` row directly a few lines below (`update_order_package`
    # with close_reason='exit_coverage_no_strategy', matching this trade's own
    # exit_reason) rather than via `_cascade_close_linked_package`. Classified
    # 2026-08-22 under BL-20260822-SEVEN-OF-EIGHT-TRADE-CLOSE-SITES-DO-NOT-CASCADE-THEIR-PACKAGE:
    # an enumeration that greps for the HELPER NAME rather than for the OUTCOME
    # (is the package closed?) reports this site as a gap. It is not one.
    try:
        db.update_trade(tid, {
            "status": "closed",
            "exit_reason": "exit_coverage_no_strategy",
            "closed_at": now_iso,
            "notes": dump_capped(notes, 2000),
        })
    except Exception as exc:  # noqa: BLE001
        summary["errors"] = summary.get("errors", 0) + 1
        logger.warning(
            "_reattach_adopted_orphans: DB close write failed for trade %s: %s",
            tid, exc,
        )
        return  # keep pending; the exchange close already succeeded, retry DB

    # Close any package still linked to this trade (best-effort).
    opid = row["order_package_id"] if _row_has(row, "order_package_id") else None
    if opid:
        try:
            db.update_order_package(opid, {
                "status": "closed",
                "close_reason": "exit_coverage_no_strategy",
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_reattach_adopted_orphans: package %s close failed for "
                "trade %s: %s", opid, tid, exc,
            )

    _PENDING_ORPHAN_NOSTRAT_CLOSE.pop(tid, None)
    summary["resolved_closed"] = summary.get("resolved_closed", 0) + 1
    logger.warning(
        "_reattach_adopted_orphans: CLOSED un-attributable orphan trade %s "
        "(%s/%s)%s — no rational exit strategy could be associated",
        tid, row["symbol"], row["direction"],
        " (dry-run skip)" if skipped == "dry_run" else "",
    )
    # Best-effort operator alert (the close is already recorded).
    try:
        from src.runtime.execution_diagnostics import enqueue_trade_close
        enqueue_trade_close(
            symbol=row["symbol"] or "?",
            account=row["account_id"],
            strategy="orphan_adopt",
            entry=row["entry_price"],
            exit_price=None,
            pnl=None,
            reason="exit_coverage_no_strategy",
        )
    except Exception:  # noqa: BLE001
        pass


def _row_has(row, key: str) -> bool:
    """True if a sqlite3.Row (or dict) carries *key*."""
    try:
        return key in row.keys()
    except AttributeError:
        return key in row


def _adopt_orphan_position(
    *,
    db,
    account_id: str,
    symbol: str,
    direction: str,
    size: float,
    entry_price: float,
) -> int:
    """Adopt an exchange-side orphan position into the journal.

    Used by :func:`_reconcile_orphan_exchange_positions` when
    ``ORPHAN_POSITION_POLICY=adopt``.

    **First choice — return it to its strategy.** We try to recover the
    order package that originally opened this position
    (:func:`_recover_orphan_order_package`, confident symbol+direction+entry
    match). On success we insert the trade row attributed to that
    **originating strategy** (carrying the package's stored SL/TP) and
    **reopen + re-link** the package, so the normal monitor loop runs that
    strategy's ``monitor()`` against it — break-even trail, level-cross /
    thesis exit, time-decay — exactly as if the journal row had never been
    lost. This is baseline correctness, not an optional mode.

    **Fallback — bare adopt.** Only when the origin can't be confidently
    recovered do we fall back to the minimal row (``strategy_name='orphan_adopt'``,
    ``setup_type='adopted_orphan'``, NULL SL/TP) that the forward reconciler
    closes when the exchange reports the position flat. We never fabricate a
    strategy attribution or synthesize stops.

    Returns the new ``trades.id``.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # Paper-vs-real-money CATEGORY for the adopted row — stamped at insert so
    # an adopted PAPER orphan is never mis-classified as real_money (the column
    # defaults — is_demo=0, account_class=NULL→real_money — would otherwise leak
    # a paper orphan into real-money PnL/stats). Canonical resolution (mirrors
    # execute.py); best-effort, falls back to real_money on any lookup failure.
    account_class = _resolve_account_class(account_id)
    is_demo = int(account_class == "paper")

    recovered = _recover_orphan_order_package(
        db=db, symbol=symbol, direction=direction, entry_price=entry_price,
    )
    if recovered is not None:
        opid = recovered.get("order_package_id")
        strategy_name = recovered.get("strategy_name")
        sl = recovered.get("sl")
        tp = recovered.get("tp")
        notes_payload = dump_capped(
            {
                "adopted_at": now_iso,
                "adopted_by": "reverse_reconciler",
                "adopted_reason": (
                    f"exchange reported an open {symbol} position on "
                    f"{account_id} with no matching trades.status='open' row; "
                    f"re-attached to originating strategy {strategy_name!r} "
                    f"via order package {opid!r}"
                ),
                "reattached_order_package_id": opid,
                "recovered_strategy": strategy_name,
                "exchange_entry_price": entry_price,
                "exchange_size": size,
            },
            500,
        )
        trade_data = {
            "timestamp": now_iso,
            "symbol": symbol,
            "direction": direction,
            "entry_price": entry_price,
            "position_size": size,
            "setup_type": "adopted_orphan",
            "entry_reason": "reverse_reconciler_reattached_to_strategy",
            "status": "open",
            "notes": notes_payload,
            "is_backtest": 0,
            "account_class": account_class,
            "is_demo": is_demo,
            "order_package_id": opid,
            "strategy_name": strategy_name,
            "stop_loss": sl,
            "take_profit_1": tp,
            "account_id": account_id,
            # Reconciled to its real strategy + order package (item #4).
            "reconcile_status": "reconciled",
        }
        trade_id = int(db.insert_trade(trade_data))
        # Reopen + re-link the original package so run_monitor_tick picks it
        # up under the recovered strategy and applies its monitor() exits.
        try:
            db.update_order_package(opid, {
                "status": "open",
                "linked_trade_id": trade_id,
                "close_reason": None,
            })
        except Exception as exc:  # noqa: BLE001 — trade row already adopted; log only
            logger.warning(
                "_adopt_orphan_position: re-link of package %s failed: %s",
                opid, exc,
            )
        # Re-arm the broker-side stop on the freshly-adopted position. The
        # recovered SL/TP went onto the journal row above, but the exchange
        # position is naked until a protective bracket is placed. Unconditional
        # baseline behaviour — part of healing the orphan. BL-20260615-MGCNAKED.
        try:
            _rearm_broker_protection_after_recovery(db, trade_id, sl, tp)
        except Exception as exc:  # noqa: BLE001 — best-effort; never abort adoption
            logger.warning(
                "_adopt_orphan_position: broker re-arm failed for trade_id=%s: %s",
                trade_id, exc,
            )
        logger.warning(
            "_adopt_orphan_position: RE-ATTACHED orphan %s/%s to strategy "
            "%s (package %s) as trade_id=%s — now under strategy monitoring",
            symbol, direction, strategy_name, opid, trade_id,
        )
        return trade_id

    # Fallback: bare orphan_adopt (origin not confidently recoverable).
    notes_payload = dump_capped(
        {
            "adopted_at": now_iso,
            "adopted_by": "reverse_reconciler",
            "adopted_reason": (
                f"exchange reported an open {symbol} position on "
                f"{account_id} with no matching trades.status='open' row; "
                "no originating order package recovered — bare adopt"
            ),
            "exchange_entry_price": entry_price,
            "exchange_size": size,
        },
        500,
    )
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
        "account_class": account_class,
        "is_demo": is_demo,
        # No order package recovered → order_package_id legitimately unset (NULL).
        "strategy_name": "orphan_adopt",
        "account_id": account_id,
        # No real package recovered — this is the red-flag state to resolve (item #4).
        "reconcile_status": "unreconciled",
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

    Runs unconditionally every monitor tick (the MONITOR_RECONCILE_ENABLED
    gate was removed 2026-06-15, BL-20260615-MGCNAKED — self-heal is baseline
    correctness). Best-effort — every step is wrapped; one bad row never
    aborts the sweep.
    """
    summary = {
        "checked": 0,
        "orphaned": 0,
        "closed": 0,
        "pending_close": 0,
        "skipped_dry": 0,
        "skipped_no_creds": 0,
        "skipped_no_cfg": 0,
        "skipped_recent": 0,
        "skipped_non_numeric": 0,
        "errors": 0,
    }

    try:
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, notes, created_at, "
                "       entry_price, position_size, setup_type "
                "  FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0"
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
    from src.runtime.positions import position_netting_guard_active_for

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
            if trade_id_str is None or not _is_real_order_id(trade_id_str):
                # Synthetic id (``rejected-…``, ``exchange_rejected-…``,
                # ``dry-…``, …) or missing — never a live exchange
                # order. Pre-2026-05-16 the gate was ``.isdigit()``
                # which also rejected Bybit V5 UUID-format orderIds
                # and silently turned this reconciler into a no-op
                # for every linear-perp account; see _is_real_order_id.
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
                        linked_package_id=_resolve_linked_package_id(
                            db, row.get("id"),
                        ),
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
            _tid = row.get("id")
            if (sym, side) in positions_cache:
                # Order filled, position still open — trade is alive.
                # Clear any pending close-confirmation: a net-flat we saw
                # on a previous tick was transient (an intent reduce/flip
                # leg or index lag), so it must NOT count toward closing
                # this row. This clear is the crux of the netting guard's
                # reconciler half — only a flat that PERSISTS closes a trade.
                if _tid is not None:
                    _PENDING_CLOSE_CONFIRM.pop(int(_tid), None)
                continue

            # Order filled, position flat → trade closed by exchange
            # (TP / SL / manual flatten). Mark closed with REAL exit
            # price + exec time from order history (closes the PnL
            # gap the legacy reconciler-close path left as
            # exit_price=NULL).
            #
            # Netting-guard (Option A, BL-20260608-DEMOPNL): when on,
            # require the flat to be confirmed across an extra grace tick
            # (a second observation, ``RECONCILER_CLOSE_CONFIRM_SECONDS``
            # apart) before closing — so reduce/flip churn and index lag
            # can't prematurely close the row and free the strategy-monocle.
            if position_netting_guard_active_for(aid) and _tid is not None:
                _tid_int = int(_tid)
                _first_flat = _PENDING_CLOSE_CONFIRM.get(_tid_int)
                if _first_flat is None:
                    # First net-flat observation — arm the confirmation and
                    # wait for the next tick to confirm.
                    _PENDING_CLOSE_CONFIRM[_tid_int] = now
                    summary["pending_close"] += 1
                    continue
                if (now - _first_flat).total_seconds() < _close_confirm_seconds():
                    # Seen flat before but the confirm window hasn't elapsed
                    # yet — keep waiting (still might recover to open).
                    summary["pending_close"] += 1
                    continue
                # Flat confirmed across the window → close, clear the pending.
                _PENDING_CLOSE_CONFIRM.pop(_tid_int, None)

            _exit_reason = "reconciler_filled"
            _close_mechanism: Optional[str] = None
            try:
                _exit_reason, _close_mechanism = _close_trade_from_order_status(
                    db, row, order_status, cfg=cfg,
                )
                summary["closed"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_reconcile_open_trades: close write failed for "
                    "trade_id=%s account=%s symbol=%s: %s",
                    row.get("id"), aid, sym, exc,
                )
                summary["errors"] += 1
                continue

            # Diagnostic ping (per-close cap + roll-up).
            # The headline and classification now reflect the exit reason
            # recovered by _close_trade_from_order_status (closed-pnl
            # lookup + _classify_broker_exit), so the operator sees "TP
            # exit" / "SL exit" instead of the generic "unknown" that the
            # old _classify_orphan_close returned for all derivatives.
            # A trade with no linked package (genuinely untracked) gets an
            # alarming "🧹 Orphaned" headline; a properly-tracked trade
            # whose bracket fired at the broker before the monitor caught
            # it gets a calm "🎯 SL/TP exit" headline instead.
            if orphan_pings_emitted < _ORPHAN_PING_CAP:
                _linked_pkg = _resolve_linked_package_id(db, row.get("id"))
                if _exit_reason == "sl":
                    _ping_headline = "🎯 Stop-loss exit detected by reconciler"
                    _ping_cls = "sl"
                    _ping_note = (
                        "Bybit SL bracket fired; detected at next reconcile tick"
                    )
                elif _exit_reason == "tp":
                    _ping_headline = "🎯 Take-profit exit detected by reconciler"
                    _ping_cls = "tp"
                    _ping_note = (
                        "Bybit TP bracket fired; detected at next reconcile tick"
                    )
                elif _linked_pkg:
                    # Refine the classification using the execType the
                    # close-from-order-status path recovered from Bybit
                    # /v5/execution/list (2026-06-25).
                    if _close_mechanism == "BustTrade":
                        _ping_cls = "broker_close_liquidation"
                        _ping_headline = "💥 Liquidation/margin call detected by reconciler"
                        _ping_note = (
                            "Bybit BustTrade — demo margin call or position liquidation; "
                            "platform closed the position, not a bot SL/TP order"
                        )
                    elif _close_mechanism == "AdlTrade":
                        _ping_cls = "broker_close_adl"
                        _ping_headline = "⚡ Auto-deleveraging close detected by reconciler"
                        _ping_note = (
                            "Bybit AdlTrade — auto-deleverage event; "
                            "platform unwound the position"
                        )
                    elif _close_mechanism is None:
                        _ping_cls = "broker_close_platform_reset"
                        _ping_headline = "🔄 Broker close: no execution found by reconciler"
                        _ping_note = (
                            "No execution record found in 10-min window — "
                            "possible demo platform reset, session expiry, or data gap; "
                            "exit price not at SL/TP levels"
                        )
                    else:
                        # "Trade" or "BlockTrade" — a real order closed the position
                        # (manual flatten or stop outside the bot's tracked bracket).
                        _ping_cls = "broker_close_unclassified"
                        _ping_headline = "🔔 Broker close detected by reconciler"
                        _ping_note = (
                            f"Closed via {_close_mechanism} order at exchange; "
                            "exit price not at SL/TP levels — "
                            "manual close or untracked stop"
                        )
                else:
                    _ping_headline = "🧹 Orphaned trade — no package link"
                    _ping_cls = "unlinked_orphan"
                    _ping_note = (
                        "Trade has no order-package link; "
                        "genuinely untracked — investigate"
                    )
                enqueue_orphan_reconciliation(
                    account=aid,
                    symbol=str(sym),
                    side=side,
                    db_trade_id=row.get("id"),
                    linked_package_id=_linked_pkg,
                    headline=_ping_headline,
                    classification=_ping_cls,
                    classification_note=_ping_note,
                )
                orphan_pings_emitted += 1
            else:
                orphan_pings_suppressed += 1

    if orphan_pings_suppressed:
        enqueue_orphan_rollup(suppressed_count=orphan_pings_suppressed)

    if (
        summary["orphaned"]
        or summary["closed"]
        or summary["pending_close"]
        or summary["errors"]
    ):
        logger.info(
            "_reconcile_open_trades: checked=%d orphaned=%d closed=%d "
            "pending_close=%d skipped_dry=%d skipped_no_creds=%d "
            "skipped_no_cfg=%d skipped_recent=%d skipped_non_numeric=%d "
            "errors=%d",
            summary["checked"], summary["orphaned"], summary["closed"],
            summary["pending_close"], summary["skipped_dry"],
            summary["skipped_no_creds"], summary["skipped_no_cfg"],
            summary["skipped_recent"], summary["skipped_non_numeric"],
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

    Runs unconditionally every monitor tick (the MONITOR_RECONCILE_ENABLED
    gate was removed 2026-06-15, BL-20260615-MGCNAKED — self-heal is baseline
    correctness). Best-effort — never raises.

    Returns:
        int: number of rows marked orphaned.
    """
    try:
        conn = db.connect()
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            # (0) Packages that ACTUALLY EXECUTED but were never linked back.
            #     A non-rejection trade row back-references the package via
            #     ``trades.order_package_id`` (the many-to-one column added in
            #     database._migrate_add_order_package_id), yet the package's
            #     one-slot ``linked_trade_id`` is NULL. This is the multi-writer
            #     fan-out gap that column was added to close: when a single
            #     decision fans out into several trade rows (real-money entry +
            #     demo mirror + intent_reduce flip leg + multi-account fanout),
            #     only the LAST update_order_package(linked_trade_id=...) survives
            #     — so a package can be left linked_trade_id NULL even though it
            #     filled. The BUG-049 sweep in step (2) keys solely on
            #     ``linked_trade_id IS NULL`` and would falsely stamp such a
            #     package 'orphaned — never executed', polluting per-strategy
            #     orphan-rate stats AND (when the linked trade is still open)
            #     wrongly terminalising a package whose position is LIVE — which
            #     clears the strategy-monocle open gate on a live position
            #     (BL-20260601-001). Reconcile from the executed trade instead:
            #     back-fill ``linked_trade_id`` and set the package status to
            #     match the trade (open→'open', else 'closed'), preferring an open
            #     trade so a live position is reflected, else the newest closed.
            #     Runs FIRST so an actual fill wins over a co-fanned rejection leg
            #     (step 1). Pure link/label reconciliation — no order is placed
            #     or modified.
            #
            #     Scope is status IN ('open','orphaned') — NOT just 'open' — so
            #     this ALSO HEALS the accumulated backlog of packages a PRIOR
            #     sweep already false-orphaned before this reconcile existed (the
            #     2026-07-26 deploy found 1744 orphaned packages, dominated by
            #     this exact executed-but-unlinked class; a status='open'-only
            #     filter left every one of them stuck 'orphaned' forever because
            #     they are no longer 'open'). Re-examining 'orphaned' rows each
            #     tick is cheap: idx_order_packages_status selects the candidate
            #     set and the correlated EXISTS rides idx_trades_order_package_id,
            #     and a healed row drops out immediately (linked_trade_id is then
            #     non-NULL). A genuinely never-executed orphan simply fails the
            #     EXISTS and is left untouched.
            conn.execute(
                "UPDATE order_packages "
                "SET linked_trade_id = ( "
                "        SELECT t.id FROM trades t "
                "        WHERE t.order_package_id = order_packages.order_package_id "
                "          AND COALESCE(t.is_backtest, 0) = 0 "
                "          AND t.status IN ('open', 'closed') "
                "        ORDER BY (t.status = 'open') DESC, t.id DESC LIMIT 1), "
                "    status = ( "
                "        SELECT CASE WHEN t.status = 'open' THEN 'open' ELSE 'closed' END "
                "        FROM trades t "
                "        WHERE t.order_package_id = order_packages.order_package_id "
                "          AND COALESCE(t.is_backtest, 0) = 0 "
                "          AND t.status IN ('open', 'closed') "
                "        ORDER BY (t.status = 'open') DESC, t.id DESC LIMIT 1), "
                "    updated_at = ?, "
                "    meta = json_set(COALESCE(meta, '{}'), "
                "        '$.executed_reconciled_at', ?, "
                "        '$.executed_reconciled_by', 'monitor_reconciler', "
                "        '$.executed_reconciled_reason', "
                "        'executed trade back-references this package "
                "(trades.order_package_id) but linked_trade_id was never written "
                "(multi-writer fan-out); reconciled from the journalled executed "
                "trade row instead of orphaning') "
                "WHERE status IN ('open', 'orphaned') "
                "  AND linked_trade_id IS NULL "
                "  AND datetime(created_at) <= datetime('now', '-5 minutes') "
                "  AND EXISTS ( "
                "        SELECT 1 FROM trades t "
                "        WHERE t.order_package_id = order_packages.order_package_id "
                "          AND COALESCE(t.is_backtest, 0) = 0 "
                "          AND t.status IN ('open', 'closed')) ",
                (now_iso, now_iso),
            )
            executed_reconciled = conn.execute("SELECT changes()").fetchone()[0]
            # (1) Packages whose refusal WAS journalled as a rejection trades row
            #     (same order_package_id, a rejected*/exchange_rejected status) are
            #     not generic orphans — the strategy fired and the RiskManager
            #     deliberately refused it (e.g. a sub-1-contract whole-contract
            #     size → rejected_too_small). Label them with the ACTUAL rejection
            #     status so a deliberate risk refusal isn't mislabelled "never
            #     executed" (misleading observability; the stranded-MGC case).
            #     Pure relabel — both 'orphaned' and the rejected* statuses are in
            #     _TERMINAL_TRADE_STATUSES, so the BUG-046 monocle gate (which only
            #     blocks on status='open') is unaffected.
            conn.execute(
                "UPDATE order_packages "
                "SET status = ( "
                "        SELECT t.status FROM trades t "
                "        WHERE t.order_package_id = order_packages.order_package_id "
                "          AND t.status IN ('rejected', 'rejected_too_small', 'exchange_rejected') "
                "        ORDER BY t.id DESC LIMIT 1), "
                "    updated_at = ?, "
                "    meta = json_set(COALESCE(meta, '{}'), "
                "        '$.rejected_at', ?, "
                "        '$.rejected_by', 'monitor_reconciler', "
                "        '$.rejected_reason', "
                "        'risk-refused (no fill placed); reconciled from the journalled rejection row') "
                "WHERE status = 'open' "
                "  AND linked_trade_id IS NULL "
                "  AND datetime(created_at) <= datetime('now', '-5 minutes') "
                "  AND EXISTS ( "
                "        SELECT 1 FROM trades t "
                "        WHERE t.order_package_id = order_packages.order_package_id "
                "          AND t.status IN ('rejected', 'rejected_too_small', 'exchange_rejected')) ",
                (now_iso, now_iso),
            )
            rejected_relabelled = conn.execute("SELECT changes()").fetchone()[0]
            # (1.5) execution:shadow packages NEVER link a trade by design
            #     (config/strategies.yaml execution: shadow — data-only: logs
            #     order packages everywhere but never sends a live order). A
            #     shadow package that never links a trade is NOT an orphan;
            #     mark it 'shadow_expired' (not 'orphaned') so shadow-soak noise
            #     never pollutes orphan-rate analytics and reviews stop chasing
            #     phantom orphans (BL-20260705-SHADOW-PKG-ORPHAN-STATUS). SQLite
            #     can't call execution_mode(), so resolve in Python over the same
            #     candidate set path (2) below matches; shadow rows are then no
            #     longer status='open', so path (2) skips them. Fail-permissive:
            #     execution_mode() unknown→'live', so only genuinely-configured
            #     shadow strategies are ever relabelled (a real orphan is never
            #     mislabelled shadow).
            shadow_relabelled = 0
            try:
                from src.strategy_registry import execution_mode as _exec_mode
                _cands = conn.execute(
                    "SELECT order_package_id, strategy_name FROM order_packages "
                    "WHERE status = 'open' "
                    "  AND linked_trade_id IS NULL "
                    "  AND datetime(created_at) <= datetime('now', '-5 minutes')"
                ).fetchall()
                _shadow_ids = [
                    r[0] for r in _cands
                    if _exec_mode(str(r[1] or "")) == "shadow"
                ]
                if _shadow_ids:
                    _ph = ",".join("?" * len(_shadow_ids))
                    conn.execute(
                        "UPDATE order_packages "
                        "SET status = 'shadow_expired', "
                        "    close_reason = 'shadow_no_execute', "
                        "    updated_at = ?, "
                        "    meta = json_set(COALESCE(meta, '{}'), "
                        "        '$.shadow_expired_at', ?, "
                        "        '$.shadow_expired_by', 'monitor_reconciler', "
                        "        '$.shadow_expired_reason', "
                        "        'execution:shadow strategy — logs packages, never executes; not an orphan') "
                        f"WHERE order_package_id IN ({_ph})",
                        (now_iso, now_iso, *_shadow_ids),
                    )
                    shadow_relabelled = conn.execute("SELECT changes()").fetchone()[0]
            except Exception as _sx:  # noqa: BLE001 — never break the sweep
                logger.debug(
                    "_sweep_unlinked_packages: shadow relabel skipped: %s", _sx
                )
            # (2) Genuinely never-dispatched packages (no fill, no rejection row) —
            #     the original BUG-049 orphan sweep.
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
            affected = (
                executed_reconciled
                + rejected_relabelled
                + shadow_relabelled
                + conn.execute("SELECT changes()").fetchone()[0]
            )
            conn.commit()
        finally:
            conn.close()
        if affected:
            logger.info(
                "_sweep_unlinked_packages: reconciled %d unlinked package(s) "
                "(%d reconciled from executed trade incl. backlog heal, %d "
                "relabelled from journalled rejection, %d shadow_expired, "
                "%d orphaned)",
                affected,
                executed_reconciled,
                rejected_relabelled,
                shadow_relabelled,
                affected - executed_reconciled - rejected_relabelled - shadow_relabelled,
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

    This sweep is the second line of defence: idempotent, runs once per
    monitor tick unconditionally (the MONITOR_RECONCILE_ENABLED gate was
    removed 2026-06-15, BL-20260615-MGCNAKED — self-heal is baseline
    correctness).

    Returns:
        int: number of rows force-closed this tick.
    """
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
            # Item #3: this second-line self-heal was log-only. A non-zero sweep
            # means a PRIMARY cascade path missed (a package stayed open after
            # its trade went terminal, blocking the strategy gate) — surface it
            # so the gap is visible, not silently patched. Best-effort.
            try:
                from src.runtime.execution_diagnostics import (
                    enqueue_stuck_package_sweep,
                )
                enqueue_stuck_package_sweep(count=affected)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_sweep_stuck_linked_packages: sweep alert enqueue failed: %s",
                    exc,
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


# PR claude/watchdog-cadence-fix-JZkeL (2026-05-16) added a flat
# ``RELEASE_STUCK_PKG_MINUTES`` (default 90) that flipped a position-
# alive package's status to ``closed`` so the strategy_monocle gate
# would reopen. The premise — that ``run_monitor_tick`` would "keep
# tracking the live position" via the existing reconciler — was
# wrong: the monitor's main query is ``status='open'``, so closing
# the package strands the trade with no strategy ``monitor()`` ticks
# for the rest of its life (no chandelier trail, no time-decay,
# nothing beyond Bybit's static server-side SL/TP). Removed
# 2026-06-07. Position-alive packages now defer indefinitely (one
# alert, stamp ``meta.stuck_alert_emitted_at``) — the pre-JZkeL
# 2026-05-09 behaviour. The strategy_monocle gate stays closed
# while the trade is alive, which is the correct one-package-per-
# strategy semantics. Any "open trade with no open package" case is
# a reconciliation concern — handled by
# ``_reconcile_orphan_exchange_positions``, not the watchdog.


# Timeframe-aware stuck threshold (2026-05-25). A package's ``updated_at``
# only advances when ``monitor()`` returns a non-None verdict (a
# Chandelier-trail ratchet / SL-TP move). On a multi-hour strategy a
# perfectly healthy live position routinely goes many minutes between
# ratchets, so the flat 30-min ``STUCK_STRATEGY_THRESHOLD_MINUTES``
# (tuned for vwap's 5m cadence) false-fires "still stuck" on good trades
# (trend_donchian 2h, fade/squeeze 4h). The watchdog scales its
# position-alive quiet window by the package's OWN bar interval so a 5m
# trade still trips at the floor while a 2h/4h trade waits proportionally
# longer; genuine orphans (position flat) are unaffected and still trip
# at the floor. Multiplier is operator-tunable without a restart.
_DEFAULT_STUCK_STRATEGY_TIMEFRAME_MULT = 3.0

_TIMEFRAME_UNIT_MINUTES = {"m": 1.0, "h": 60.0, "d": 1440.0, "w": 10080.0}


def _stuck_strategy_timeframe_mult() -> float:
    """Read ``STUCK_STRATEGY_TIMEFRAME_MULT`` at call time. Number of
    bar-intervals of silence before a position-alive package is treated
    as stuck. Default 3; clamped to ``>= 1`` (a sub-1 multiple would
    fight the per-bar verdict cadence). Unparseable → default.
    """
    raw = os.environ.get("STUCK_STRATEGY_TIMEFRAME_MULT")
    if raw is None or str(raw).strip() == "":
        return _DEFAULT_STUCK_STRATEGY_TIMEFRAME_MULT
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_STUCK_STRATEGY_TIMEFRAME_MULT


def _timeframe_to_minutes(timeframe: Any) -> Optional[float]:
    """Parse a strategy timeframe (``"5m"``, ``"2h"``, ``"4h"``, ``"1d"``)
    into minutes. A bare integer is treated as minutes (the CCXT-style
    ``"120"`` == 120 m convention used elsewhere). Returns ``None`` on
    missing / unparseable input so the caller falls back to the flat
    floor.
    """
    if timeframe is None:
        return None
    s = str(timeframe).strip().lower()
    if not s:
        return None
    if s.isdigit():
        v = float(s)
        return v if v > 0 else None
    unit = _TIMEFRAME_UNIT_MINUTES.get(s[-1])
    if unit is None:
        return None
    try:
        n = float(s[:-1])
    except ValueError:
        return None
    return n * unit if n > 0 else None


def _stuck_threshold_for_package(meta: Optional[Dict[str, Any]]) -> float:
    """Per-package stuck threshold (minutes), timeframe-aware.

    ``max(floor, mult x timeframe_minutes)`` where the floor is the env
    ``STUCK_STRATEGY_THRESHOLD_MINUTES`` (default 30). Packages without a
    parseable ``meta.timeframe`` fall back to the flat floor — the
    pre-2026-05-25 behaviour — so short-timeframe strategies keep
    tripping quickly and genuinely-stuck rows are still caught.
    """
    floor = _stuck_strategy_threshold_minutes()
    tf_min = _timeframe_to_minutes((meta or {}).get("timeframe"))
    if tf_min is None:
        return floor
    return max(floor, _stuck_strategy_timeframe_mult() * tf_min)


def _pkg_age_minutes(updated_at: Any) -> Optional[float]:
    """Return age (in minutes) of an order_packages row given the
    raw ``updated_at`` string. ``None`` on unparseable input — the
    caller treats that as "skip the release check" so a malformed
    timestamp doesn't drive a force-close.
    """
    if not updated_at:
        return None
    try:
        ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return delta.total_seconds() / 60.0


#: Historical ``exit_price_source`` stamp, used when a broker closed-pnl record
#: predates the ``source`` field (2026-07-30). Bybit was the only reader then,
#: so this is the correct — and only possible — value for such a record.
_LEGACY_BROKER_PNL_SOURCE = "bybit_closed_pnl"


def _broker_pnl_source(rec: Optional[Dict[str, Any]]) -> str:
    """The provenance stamp for a broker closed-pnl record.

    Every caller used to hardcode ``"bybit_closed_pnl"``. That was accurate
    while ``BROKER_PNL_READER_EXCHANGES`` held only ``bybit`` — and became a
    provenance LIE the moment IB was wired, labelling an IBKR execution as a
    Bybit closed-pnl record. The record now declares its own source
    (``clients.account_closed_pnl_for_trade``); read it, never a literal.

    Falls back to the historical value for a record with no ``source``, which
    can only have come from the Bybit reader.
    """
    src = (rec or {}).get("source") if isinstance(rec, dict) else None
    return str(src) if src else _LEGACY_BROKER_PNL_SOURCE


def _broker_pnl_note_key(rec: Optional[Dict[str, Any]]) -> str:
    """Notes key the raw broker PnL figure is filed under.

    Kept as ``bybit_closed_pnl`` for Bybit so every existing reader of that key
    (and every historical row) is unaffected; a non-Bybit reader files under
    ``<source>`` so the two are never conflated in one field.
    """
    src = _broker_pnl_source(rec)
    return "bybit_closed_pnl" if src.startswith("bybit") else src


#: How much bigger a closed-pnl record's qty must be than a row's own qty before
#: the record is treated as covering a NETTED position rather than that row
#: alone. 5% absorbs venue rounding without letting a genuinely larger flatten
#: through.
NETTED_PRORATE_QTY_RATIO = 1.05


def _prorate_netted_broker_pnl(
    rec_pnl: Any,
    rec_qty: Any,
    row_qty: Any,
    *,
    always: bool = False,
) -> tuple[Optional[float], bool]:
    """Split a netted closed-pnl record down to one row's qty share.

    Returns ``(pnl, prorated)``. ``pnl`` is ``None`` when nothing is bookable;
    ``prorated`` says whether the split actually happened, so the caller stamps
    the provenance it owns (the three call sites label differently on purpose —
    see below — and this helper deliberately does not choose for them).

    **Why this is one function.** Under one-way netting the broker returns ONE
    closed-pnl record for the whole netted position, and booking its full value
    onto each journal row that shared that position fabricates: the same figure
    lands on N rows whose quantities differ by orders of magnitude. Measured on
    the live journal 2026-08-06 — ``pnl = -2970.99`` on three
    htf_pullback_trend_2h/BTCUSDT rows of qty 0.012 / 0.717 / 0.728, the first
    implying a ~$247,000 BTC move. It surfaced only because the P1.x real-R axis
    normalises pnl by each row's own risk and turned it into a −99.5R outlier;
    raw-USD aggregates had absorbed it silently for months.

    The guard already existed at TWO of the three sites that persist a broker
    close (BL-20260720-ICTSCALP-PASTSTOP-EXITS for the reconciler, and the
    netted-cascade path) and was **missing from the third**,
    :func:`_recover_close_from_broker_pnl`, which stamps
    ``exit_price_source`` = the broker source and therefore classified the
    fabricated value as MEASURED. Three hand-rolled copies is how the fourth
    site diverges, which is the same "one module owns this" lesson
    ``src/runtime/provenance.py`` was created for.

    ``always=True`` is the CASCADE semantics: those rows are siblings of a
    known netted flatten by construction, so the record covers them all and the
    ratio test would wrongly skip a sibling whose qty happens to match the
    record. Default (``always=False``) is the reconciler semantics: prorate only
    when the record is materially bigger than this row
    (``rec_qty > row_qty * NETTED_PRORATE_QTY_RATIO``), so a normal 1:1 close
    keeps the broker's exact figure rather than being re-derived through a
    division that can only lose precision.
    """
    pnl = _safe_float(rec_pnl)
    if pnl is None:
        return None, False
    rq = _safe_float(rec_qty) or 0.0
    wq = _safe_float(row_qty) or 0.0
    if rq <= 0 or wq <= 0:
        return float(pnl), False
    if always or rq > wq * NETTED_PRORATE_QTY_RATIO:
        return float(pnl) * (wq / rq), True
    return float(pnl), False


def _recover_close_from_broker_pnl(
    db,
    trade_row: Any,
    cfg: Optional[Dict[str, Any]],
    now_iso: str,
) -> Optional[Dict[str, Any]]:
    """Best-effort: turn a position-flat trade into a finalised CLOSE using
    the broker's authoritative closed-pnl record, instead of orphaning it.

    Used by :func:`_watchdog_stuck_strategies` for the position-flat branch.
    Mirrors the recovery the forward reconciler's close path
    (:func:`_close_trade_from_order_status`) and the one-shot
    ``scripts/ops/backfill_orphan_pnl.py`` already perform — same
    :func:`account_closed_pnl_for_trade` lookup, same write shape.

    Returns the ``update_trade`` dict (status='closed' + exit_price + pnl +
    closed_at + audit notes) on a confident broker close, or ``None`` when
    recovery isn't possible/safe (no cfg, no broker reader, lookup miss,
    degenerate/missing fill) so the caller falls back to the legacy orphan
    mark. Fail-safe: every failure path returns ``None``; it never raises.
    """
    if cfg is None or trade_row is None:
        return None
    try:
        from src.units.accounts.clients import (
            account_closed_pnl_for_trade,
            account_has_broker_pnl_reader,
        )
        # Only broker-truth integrations (Bybit today) have a closed-pnl
        # endpoint to recover from; everything else stays on the orphan path
        # (the local-PnL sweep fills those rows' pnl separately).
        if not account_has_broker_pnl_reader(cfg):
            return None

        opened_at_ms = _isoformat_to_ms(trade_row["created_at"])
        if opened_at_ms is None:
            return None

        rec = account_closed_pnl_for_trade(
            cfg,
            symbol=str(trade_row["symbol"] or ""),
            direction=str(trade_row["direction"] or ""),
            opened_at_ms=opened_at_ms,
            qty=_safe_float(trade_row["position_size"]),
            entry_price=_safe_float(trade_row["entry_price"]),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; fall back to orphan
        logger.warning(
            "_recover_close_from_broker_pnl: lookup raised for trade_id=%s: %s",
            (trade_row["id"] if trade_row is not None else None), exc,
        )
        return None

    if rec is None:
        return None
    avg_exit_price = rec.get("avg_exit_price")
    if not avg_exit_price or float(avg_exit_price) <= 0:
        return None
    closed_pnl = rec.get("closed_pnl")
    if closed_pnl is None:
        return None

    # Normalise Bybit's epoch-ms close time to ISO-8601 before persisting so
    # the trades.closed_at column contract stays ISO for every consumer
    # (BL-20260620-RECONCILER-CLOSEDAT-MS). normalize_closed_at_value passes ISO
    # through and returns None on empty/unparseable → fall back to now_iso.
    closed_at = normalize_closed_at_value(rec.get("closed_at")) or now_iso
    notes = _decode_notes(trade_row["notes"])
    # Classify the recovered broker close as sl/tp (2026-06-23) — same as the
    # reconciler close path. Exclude intent_reduce legs (deliberate partial
    # close, possibly-inverted bracket) so they keep 'reconciler_filled'.
    _cols = trade_row.keys() if hasattr(trade_row, "keys") else ()
    _is_reduce = bool(notes.get("intent_reduce")) or (
        "setup_type" in _cols
        and str(trade_row["setup_type"] or "").lower() == "intent_reduce"
    )
    _row_for_classify = {
        "id": trade_row["id"],
        "symbol": (trade_row["symbol"] if "symbol" in _cols else None),
        "direction": (trade_row["direction"] if "direction" in _cols else None),
    }
    resolved_reason = _classify_broker_exit(
        db, _row_for_classify, avg_exit_price, is_reduce_leg=_is_reduce,
    )
    final_exit_reason = resolved_reason or "reconciler_filled"
    notes.update({
        "closed_at": closed_at,
        "closed_by": "stuck_strategy_watchdog",
        "closed_reason": (
            "watchdog — position flat at exchange; recovered the real close "
            "from Bybit closed-pnl rather than orphaning"
        ),
        # The record carries its own provenance (`rec["source"]`); stamping a
        # literal here was correct only while Bybit was the sole reader, and
        # became a provenance LIE the moment IB was wired (2026-07-30). A
        # pre-`source` record falls back to the historical value.
        "exit_price_source": _broker_pnl_source(rec),
        _broker_pnl_note_key(rec): closed_pnl,
        "exit_reason_source":
            ("price_vs_pkg_bracket" if resolved_reason else "unresolved"),
    })
    entry = _safe_float(trade_row["entry_price"])
    qty = _safe_float(trade_row["position_size"])
    # Netted-position proration (BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS).
    # This path lacked the guard its two sibling persist sites already had, so a
    # record that flattened a BIGGER netted position was booked WHOLE onto this
    # row — and stamped with the broker source above, which classifies MEASURED.
    # A fabricated figure wearing a measured label is strictly worse than a
    # missing one: every consumer folds it into an aggregate unchallenged.
    pnl_to_book, prorated = _prorate_netted_broker_pnl(
        closed_pnl, rec.get("qty"), qty,
    )
    if prorated:
        notes["pnl_source"] = "netted_prorated"
        notes["bybit_closed_pnl_record_total"] = closed_pnl
        # Downgrade the exit-price stamp too. The exit PRICE is still the
        # broker's, but `_prorated` is what provenance.classify_pnl reads to
        # bucket the row FABRICATED — the split is an assumption about
        # attribution however measured the underlying record was, and leaving
        # the bare broker source here would keep calling it MEASURED.
        notes["exit_price_source"] = f"{_broker_pnl_source(rec)}_prorated"
    updates: Dict[str, Any] = {
        "status": "closed",
        "exit_reason": final_exit_reason,
        "exit_price": float(avg_exit_price),
        "closed_at": closed_at,
    }
    if pnl_to_book is not None:
        updates["pnl"] = round(pnl_to_book, 4)
        if entry and qty and entry * qty > 0:
            updates["pnl_percent"] = round(
                pnl_to_book / (entry * qty) * 100, 4
            )
    updates["notes"] = dump_capped(notes, 500)
    return updates


def _watchdog_stuck_strategies(db) -> Dict[str, int]:
    """Detect + recover packages stuck at ``status='open'`` AND
    ``linked_trade_id IS NOT NULL`` for longer than the configured
    threshold.

    For each stuck package, cross-check with the exchange-side
    position view via :func:`account_open_positions` (cached per
    account per tick) before deciding what to do:

      * **Position alive at exchange** (the ``(symbol, direction)``
        pair shows up in the exchange's position list) → never
        touch the package or trade row beyond a meta stamp
        (``stuck_alert_emitted_at``) and one operator alert. The
        package row stays ``status='open'`` so the strategy's
        ``monitor()`` hook keeps firing on every tick (trailing
        SL, time-decay close, structure exit). The
        strategy_monocle gate stays closed for that strategy —
        which is the correct "one open package per strategy"
        semantics while a multi-hour trade rides. Pre-2026-06-07
        a ``RELEASE_STUCK_PKG_MINUTES`` knob flipped the package
        to ``closed`` after 90 min; that stranded the trade with
        no strategy monitoring and was removed.

      * **Position flat at exchange** (read succeeded, no matching
        position) → genuine orphan. Force-close the package
        (``status='closed'``, ``close_reason='stuck_strategy_watchdog'``),
        cascade the linked trade row to ``status='orphaned'``, emit
        the high-priority alert. The pre-2026-05-16 daily-orphan
        cluster on bybit_2 was upstream: ``_is_numeric_order_id``
        rejected every Bybit V5 UUID-format orderId so
        :func:`_reconcile_open_trades` silently skipped these
        trades and the watchdog inherited an exchange-side-closed
        position as a "true orphan". Fix in the same PR. This
        branch is now a genuine last-resort safety net.

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

    The whole helper runs unconditionally every monitor tick (the
    MONITOR_RECONCILE_ENABLED gate was removed 2026-06-15,
    BL-20260615-MGCNAKED — self-heal is baseline correctness).

    Returns a summary
    ``{checked, alerted, auto_cleared, deferred_position_alive,
       deferred_below_timeframe, skipped_position_read_failed,
       errors}`` so the caller can log a per-tick line when
    non-zero.
    """
    summary = {
        "checked": 0,
        "alerted": 0,
        "auto_cleared": 0,
        # Position-flat trades rescued from the orphan path by a confirmed
        # broker closed-pnl recovery (finalised status='closed' with real
        # exit_price+pnl rather than orphaned with NULL pnl).
        "recovered_closed": 0,
        # Position-flat trades finalised status='closed' with a LOCAL-compute
        # pnl (the _sweep_local_pnl_for_unpriced pass fills it next tick) when
        # broker closed-pnl couldn't be matched — the expected per-leg case on
        # a one-way netting account. Replaces the old orphan+red-flag path
        # (BL-20260620-WATCHDOGORPHAN watchdog half).
        "closed_local_unmatched": 0,
        "deferred_position_alive": 0,
        # Position-alive packages skipped because they haven't been
        # silent for their TIMEFRAME-scaled quiet window yet (a healthy
        # 2h/4h trade that simply hasn't ratcheted) — no alert, no churn.
        "deferred_below_timeframe": 0,
        # Position-flat packages deferred for a 2nd confirming flat read
        # (BL-20260708-WATCHDOG-FALSEFLAT-FLAP) so a transient/partial exchange
        # snapshot can't false-close a still-live position.
        "deferred_flat_confirm": 0,
        "skipped_position_read_failed": 0,
        "errors": 0,
    }

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
                    "SELECT id, status, notes, account_id, symbol, "
                    "       direction, position_size, entry_price, created_at "
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
            # Trade is alive at the exchange. Defer indefinitely —
            # never close the package, never cascade the trade row.
            #
            # Timeframe-aware quiet window: a position-alive package
            # is only "stuck" once it has been silent for its own
            # bar-interval-scaled threshold (max(floor, mult x
            # timeframe)). A healthy multi-hour trade that hasn't
            # ratcheted its trail within the floor window is NOT
            # stuck — skip silently (no alert, no meta churn).
            # Genuine orphans never reach here (handled by the
            # position-flat branch below at the floor), so this
            # only quiets benign alerts.
            # A position that reads alive resets any pending flat-confirm
            # stamp — a flap (flat read → alive read) must restart the
            # 2-observation counter, never carry a stale "first flat" forward.
            if trade_id is not None:
                _PENDING_WATCHDOG_FLAT_CONFIRM.pop(int(trade_id), None)

            pkg_threshold = _stuck_threshold_for_package(meta)
            age_minutes = _pkg_age_minutes(row["updated_at"])
            if age_minutes is not None and age_minutes < pkg_threshold:
                summary["deferred_below_timeframe"] += 1
                continue

            summary["deferred_position_alive"] += 1
            try:
                if not already_alerted:
                    updated_meta = dict(meta)
                    updated_meta["stuck_alert_emitted_at"] = now_iso
                    updated_meta["stuck_position_alive_seen_at"] = now_iso
                    db.update_order_package(pkg_id, {"meta": updated_meta})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_watchdog_stuck_strategies: position-alive meta-stamp "
                    "failed for pkg_id=%s: %s",
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
                        # Report the per-package (timeframe-aware)
                        # threshold actually waited, not the flat floor.
                        stuck_minutes=int(pkg_threshold),
                        # We did NOT clear the gate — the trade is
                        # alive and the strategy keeps monitoring it.
                        auto_cleared=False,
                        # Confirmed alive at the exchange → informational
                        # ping, NOT the "investigate a reconciler skip"
                        # wording (this branch never touches the trade).
                        position_alive=True,
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

        # Position reads flat at the exchange. BUT a SINGLE flat read is not
        # proof — a transient/partial Alpaca positions snapshot that momentarily
        # omits a symbol is a FALSE flat (the alpaca_paper QQQ #3249→#3269 flap:
        # finalized closed with a fabricated PnL on a false flat, then the still-
        # live exchange position was re-adopted 23 min later as a naked orphan).
        # Require a 2-observation confirm (``_close_confirm_seconds`` apart), the
        # SAME discipline the reverse reconciler's close-on-disappear already
        # uses, before finalizing — keyed by trade id, cleared above the instant
        # the position reads alive again. Best-effort: an unkeyable row (no
        # trade_id) can't be double-confirmed, so it falls through as before.
        if trade_id is not None:
            tid_confirm = int(trade_id)
            _first_flat = _PENDING_WATCHDOG_FLAT_CONFIRM.get(tid_confirm)
            _now_dt = datetime.now(timezone.utc)
            if _first_flat is None:
                _PENDING_WATCHDOG_FLAT_CONFIRM[tid_confirm] = _now_dt
                summary["deferred_flat_confirm"] = (
                    summary.get("deferred_flat_confirm", 0) + 1
                )
                logger.warning(
                    "_watchdog_stuck_strategies: %s/%s/%s reads flat at exchange "
                    "— pending finalize (awaiting 2nd confirming flat read, "
                    "%.0fs apart) so a transient/partial snapshot can't false-"
                    "close a still-live position",
                    aid, symbol, direction, _close_confirm_seconds(),
                )
                continue
            if (_now_dt - _first_flat).total_seconds() < _close_confirm_seconds():
                summary["deferred_flat_confirm"] = (
                    summary.get("deferred_flat_confirm", 0) + 1
                )
                continue
            # Confirmed flat across >= 2 observations → safe to finalize.
            _PENDING_WATCHDOG_FLAT_CONFIRM.pop(tid_confirm, None)

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
        #
        # BL-20260620-WATCHDOGORPHAN: a position that reads FLAT at a
        # broker-truth exchange (Bybit) most often means the broker-side
        # SL/TP already CLOSED it — a real exit with a realised PnL Bybit
        # still holds in /v5/position/closed-pnl (7-day retention). The
        # forward reconciler (_reconcile_open_trades) finalises that case
        # as status='closed' via _close_trade_from_order_status, but it
        # is bypassed whenever account_order_status can no longer resolve
        # the aged entry order (returns not_found). The trade then survives
        # to here and was force-marked 'orphaned' with NULL pnl — invisible
        # to /api/bot/trades/closed and unmatched against Bybit's own
        # closed-pnl (the bybit_2 "missing close" the scripts/ops/
        # backfill_orphan_pnl.py one-shot was written to repair after the
        # fact). PR #1299 closed the forward-reconciler half of this but
        # left this watchdog branch un-recovered, so new orphans of the
        # exact shape kept appearing (e.g. trade #2708, 2026-06-19).
        #
        # So before orphaning, try the same closed-pnl recovery the close
        # path uses: if Bybit confirms a real close, finalise the row as
        # 'closed' with the recovered exit_price + pnl + closed_at. Only
        # when no broker close record exists (lookup None / no broker
        # reader / read error) do we fall back to the legacy 'orphaned'
        # mark. Fail-safe: any error in the recovery attempt drops cleanly
        # to the orphan path — the gate has already cleared (package
        # force-closed above), so the trader is never stranded.
        try:
            if trade_row and str(trade_row["status"]) == "open":
                recovered = _recover_close_from_broker_pnl(
                    db, trade_row, cfg, now_iso,
                )
                if recovered is not None:
                    db.update_trade(int(trade_row["id"]), recovered)
                    _cascade_close_linked_package(
                        db, trade_row["id"],
                        close_reason=recovered.get(
                            "exit_reason", "reconciler_filled"),
                        caller="_watchdog_stuck_strategies(broker_pnl_recovery)",
                    )
                    summary["recovered_closed"] = (
                        summary.get("recovered_closed", 0) + 1
                    )
                    logger.warning(
                        "_watchdog_stuck_strategies: RECOVERED close via "
                        "broker closed-pnl instead of orphaning — trade_id=%s "
                        "account=%s symbol=%s side=%s pnl=%s (position flat at "
                        "exchange; Bybit closed-pnl confirmed a real close)",
                        trade_row["id"], aid, symbol, direction,
                        recovered.get("pnl"),
                    )
                else:
                    # Position CONFIRMED FLAT at the exchange + package stuck >
                    # threshold, but broker closed-pnl couldn't be MATCHED to
                    # this leg. On a one-way NETTING account (e.g. bybit_2) that
                    # is the EXPECTED case, not an anomaly: N strategies each hold
                    # their OWN per-strategy trade row while the exchange holds
                    # ONE net position, so Bybit's closed-pnl records the NET
                    # close, never a per-leg 0.002/0.003 close the matcher can
                    # find. The position is genuinely flat → the leg DID close;
                    # the only thing we lack is broker-truth PnL.
                    #
                    # Finalising it 'orphaned' (NULL pnl + a loud "needs
                    # reconciliation" ping) was the wrong call: it red-flags a
                    # trade that actually closed, hides it from
                    # /api/bot/trades/closed, and fires the ping on EVERY netting
                    # leg — training the operator to ignore the orphan alert (the
                    # recurring bybit_2 BTCUSDT orphan noise; the watchdog half of
                    # BL-20260620-WATCHDOGORPHAN that PR #1299 left un-recovered).
                    #
                    # Finalise as 'closed' instead. The existing
                    # _sweep_local_pnl_for_unpriced pass already scans
                    # status IN ('closed','orphaned') and fills pnl +
                    # pnl_source='local_compute' next tick — so this becomes a
                    # clean local-compute close (visible in /trades/closed, no
                    # red-flag ping) exactly as it would have been priced as an
                    # orphan, minus the false alarm. The operator's
                    # exchange-history export still corrects the exact PnL later.
                    # (A genuinely anomalous close is still WARNING-logged below.)
                    trade_notes = _decode_notes(trade_row["notes"])
                    trade_notes.update({
                        "closed_at": now_iso,
                        "closed_by": "stuck_strategy_watchdog",
                        "closed_reason": (
                            "watchdog — position flat at exchange; broker "
                            "closed-pnl unmatched (per-leg on a netting "
                            f"account); package stuck > {int(threshold_minutes)} "
                            "min; finalized closed with local-compute pnl"
                        ),
                    })
                    # package-cascade: HANDLED, not missing. The package was
                    # already force-closed ABOVE on this same branch
                    # (`update_order_package` with
                    # close_reason='stuck_strategy_watchdog', matching this
                    # trade's exit_reason) — see the "Force-close the package +
                    # cascade the trade" comment. This is the cascade-the-trade
                    # half. Classified 2026-08-22 under
                    # BL-20260822-SEVEN-OF-EIGHT-TRADE-CLOSE-SITES-DO-NOT-CASCADE-THEIR-PACKAGE.
                    db.update_trade(int(trade_row["id"]), {
                        "status": "closed",
                        "exit_reason": "stuck_strategy_watchdog",
                        "closed_at": now_iso,
                        "notes": dump_capped(trade_notes, 500),
                    })
                    summary["closed_local_unmatched"] += 1
                    logger.warning(
                        "_watchdog_stuck_strategies: FINALIZED CLOSED "
                        "(local-compute pnl) instead of orphaning — trade_id=%s "
                        "account=%s symbol=%s side=%s (position flat at exchange; "
                        "broker closed-pnl unmatched — expected per-leg on a "
                        "netting account; _sweep_local_pnl_for_unpriced prices it "
                        "next tick)",
                        trade_row["id"], aid, symbol, direction,
                    )
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
        or summary["recovered_closed"]
        or summary["deferred_position_alive"]
        or summary["deferred_below_timeframe"]
        or summary["skipped_position_read_failed"]
    ):
        logger.info(
            "_watchdog_stuck_strategies: checked=%d alerted=%d "
            "auto_cleared=%d recovered_closed=%d deferred_position_alive=%d "
            "deferred_below_timeframe=%d "
            "skipped_position_read_failed=%d "
            "errors=%d (floor=%d min mult=%.1f)",
            summary["checked"], summary["alerted"], summary["auto_cleared"],
            summary["recovered_closed"],
            summary["deferred_position_alive"],
            summary["deferred_below_timeframe"],
            summary["skipped_position_read_failed"],
            summary["errors"], int(threshold_minutes),
            _stuck_strategy_timeframe_mult(),
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
    present. Best-effort — returns None on any decode failure.

    Production note (2026-05-16): the live writer in
    ``_log_trade_to_journal`` does **not** stamp ``order_package_id``
    into ``notes`` — it only writes ``trade_id`` (the exchange order
    id). The canonical journal-side trade↔package link is
    ``order_packages.linked_trade_id``; use
    :func:`_resolve_linked_package_id` for production lookups.
    This helper survives only for legacy fixtures / older trade rows
    that did stamp the package id into notes.

    Pre-2026-05-16 the function fell back to ``notes.get('trade_id')``
    when ``order_package_id`` was missing — that returned the Bybit
    UUID, which was then passed to ``db.update_order_package(pkg_id)``
    and silently no-op'd because no row matched. The fallback was
    removed so the cascade no-op is replaced by an honest None.
    """
    if not notes_raw:
        return None
    try:
        notes = json.loads(notes_raw)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(notes, dict):
        return None
    pkg_id = notes.get("order_package_id")
    if pkg_id is None:
        return None
    s = str(pkg_id).strip()
    return s or None


def _resolve_linked_package_id(db, trade_id: Any) -> Optional[str]:
    """Resolve the ``order_packages.order_package_id`` for *trade_id*.

    The trade↔package link is now stored both directions:

    1. ``trades.order_package_id`` (many-to-one) — canonical, set by
       the writer in ``execute.py`` for **every** trade row produced
       by a decision (real entry + demo mirror + intent_reduce flip
       leg + multi-account fanout). Tried first.
    2. ``order_packages.linked_trade_id`` (one-to-one, "primary entry
       trade") — legacy fallback for trade rows that pre-date the
       column. The writer also still maintains this for the primary
       leg so the strategy_monocle gate keeps working.

    Pre-fix this only consulted (2), and a multi-leg fanout's
    secondary legs (demo, intent_reduce) failed to resolve because
    only the last writer survived the race for the single slot.

    Returns ``None`` on any read failure or when no package is
    linked. Best-effort — never raises.
    """
    if trade_id is None:
        return None
    # (1) canonical many-to-one column on the trade row itself.
    try:
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT order_package_id FROM trades WHERE id = ?",
                (int(trade_id),),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_linked_package_id: trades read failed for "
            "trade_id=%s: %s",
            trade_id, exc,
        )
        row = None
    if row is not None:
        pkg_id = row[0] if not isinstance(row, dict) else row.get("order_package_id")
        if pkg_id:
            return str(pkg_id)

    # (2) legacy back-compat — pre-column rows have order_package_id
    # NULL on the trade side; fall back to the one-way link on the
    # package side.
    try:
        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT order_package_id FROM order_packages "
                "WHERE linked_trade_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (int(trade_id),),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_linked_package_id: order_packages fallback "
            "failed for trade_id=%s: %s",
            trade_id, exc,
        )
        return None
    if row is None:
        return None
    pkg_id = row[0] if not isinstance(row, dict) else row.get("order_package_id")
    if not pkg_id:
        return None
    return str(pkg_id)


def _cascade_close_linked_package(
    db,
    trade_id: Any,
    *,
    close_reason: str,
    caller: str,
) -> bool:
    """Close the order_packages row linked to *trade_id*.

    Replaces the legacy ``_extract_package_id(notes) →
    update_order_package`` pattern that silently no-op'd in
    production because ``notes`` didn't carry ``order_package_id``.
    Uses the canonical ``linked_trade_id`` lookup instead.

    Returns True when a package row was updated. ``False`` on lookup
    miss or update failure — caller should not crash on either;
    ``_sweep_stuck_linked_packages`` remains the safety net for
    cascade misses. *caller* labels the log line for diagnostics.
    """
    pkg_id = _resolve_linked_package_id(db, trade_id)
    if not pkg_id:
        return False
    try:
        affected = db.update_order_package(pkg_id, {
            "status": "closed",
            "close_reason": close_reason,
        })
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "%s: package cascade failed for pkg_id=%s linked to trade_id=%s: %s",
            caller, pkg_id, trade_id, exc,
        )
        return False
    if not affected:
        logger.warning(
            "%s: package cascade no-op for pkg_id=%s linked to trade_id=%s "
            "(row not found — stale link?)",
            caller, pkg_id, trade_id,
        )
        return False
    return True


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


# Trade-id prefixes the executor stamps when no live exchange order
# ever existed. The reconciler must skip these — handing them to
# ``account_order_status`` would either 404 or (worse) collide with
# an unrelated live order. Kept in sync with the synthesis sites in
# ``src/units/accounts/execute.py`` and ``src/units/accounts/integrator.py``.
_SYNTHETIC_TRADE_ID_PREFIXES = (
    "dry-",                  # _log_trade_to_journal dry-run path
    "rejected-",             # risk-manager rejection synthesis
    "exchange_rejected-",    # post-place exchange refusal synthesis
    "open-",                 # _log_trade_to_journal status-prefixed fallback
    "closed-",               # idem
    "dry-bybit-",            # legacy integrator paths
    "dry-breakout-",
)


def _is_real_order_id(trade_id: str) -> bool:
    """Return True when *trade_id* looks like an exchange-issued order
    id the reconciler can hand back to ``account_order_status``.

    Bybit V5 returns orderIds in two shapes depending on the endpoint
    and account type: a long digit-only string (``1842564317108924672``)
    on some flows and a UUID-shaped string
    (``bbfcde38-82db-4621-b400-9b9a7fa0b313``) on others. Both are
    valid lookup keys for ``/v5/order/realtime`` and
    ``/v5/order/history``.

    The journal also writes synthetic identifiers for rows that never
    became live orders (``dry-<hex>``, ``rejected-<hex>``,
    ``exchange_rejected-<hex>``, …). Those must be skipped.

    Previous name: ``_is_numeric_order_id``. Pre-2026-05-16 the
    function required ``.isdigit()``, which silently rejected every
    valid Bybit V5 UUID-format orderId. The reconciler's
    ``skipped_non_numeric`` counter swallowed every vwap/bybit_2
    trade, leaving the stuck-strategy watchdog as the only writer
    that ever touched these rows — and it orphaned them at 30 min
    with ``exit_price=NULL``. See PR #1xxx + diag #1252 for the
    journal evidence.
    """
    if not trade_id:
        return False
    s = str(trade_id).strip()
    if not s:
        return False
    for prefix in _SYNTHETIC_TRADE_ID_PREFIXES:
        if s.startswith(prefix):
            return False
    return True


# Backwards-compatible alias for any out-of-tree caller. Tests inside
# this repo import the new name directly.
_is_numeric_order_id = _is_real_order_id


#: `exit_reason_source` value to stamp, keyed on the PRICE basis the label was
#: derived from. The label is only ever as good as the price it was compared
#: against, and a reader must be able to tell the two apart WITHOUT re-deriving
#: the price's provenance from a second field.
#:
#: `price_vs_pkg_bracket` (a MEASURED price) is the pre-existing value and is
#: deliberately unchanged — it is already registered in
#: `provenance.ESTIMATED_SOURCES`, on the reasoning that the venue never said
#: "this was a stop-out", so even off a real fill the REASON is a derivation.
#: The `_est_price` sibling is that same derivation over an ESTIMATED price
#: (`candle_at_close`) — an inference on an inference, and it must not be
#: allowed to read as the stronger one.
_EXIT_LABEL_SOURCE_BY_BASIS: Dict[str, str] = {
    "measured": "price_vs_pkg_bracket",
    "estimated": "price_vs_pkg_bracket_est_price",
}


def _classify_broker_exit(
    db, row: Dict[str, Any], exit_price: Any, *, is_reduce_leg: bool = False,
) -> Optional[str]:
    """Classify a reconciler-finalised broker close as 'sl' or 'tp'.

    Returns ``None`` for an intent_reduce leg (``is_reduce_leg=True``): a reduce
    is a deliberate partial close, not a bracket hit, and the package bracket can
    be inverted relative to the reduce-order direction (a long leg reducing a
    short → sl above / tp below), so classifying it would mislabel the reduce as
    sl/tp. Reduce legs keep ``reconciler_filled``.

    2026-06-23 fix: on a Bybit linear account the SL/TP bracket is attached at
    entry (execute.py), so a stop/target fires at the broker INTRATICK — before
    the per-tick / per-bar strategy monitor re-evaluates. The reconciler then
    finalises the DB row, and the legacy code hard-coded ``reconciler_filled``,
    discarding whether the close was a stop or a target. Every clean exit must
    be NOTED with its true reason (operator rule), so classify it here from the
    recovered exit price vs the package's bracket levels.

    Bybit's closed-pnl record carries NO authoritative SL-vs-TP field, so we use
    a CONSERVATIVE inequality that cannot mislabel a mid-range / manual flatten:
      * long  → exit <= sl ⇒ 'sl'   (price fell to/through the stop; fills can slip
                exit >= tp ⇒ 'tp'    through the level, so '<=' / '>=' not '==')
      * short → exit >= sl ⇒ 'sl'
                exit <= tp ⇒ 'tp'
    Anything strictly between the bracket levels returns ``None`` → the caller
    keeps ``reconciler_filled`` (a genuine non-bracket close — the real
    "flag it" residue). Best-effort; never raises.
    """
    if is_reduce_leg:
        return None
    px = _safe_float(exit_price)
    if not px or px <= 0:
        return None
    direction = str(row.get("direction") or "").lower()
    if direction not in ("long", "short"):
        return None
    sl = tp = None
    try:
        pkg_id = _resolve_linked_package_id(db, row.get("id"))
        if pkg_id:
            conn = db.connect()
            try:
                conn.row_factory = __import__("sqlite3").Row
                prow = conn.execute(
                    "SELECT sl, tp FROM order_packages WHERE order_package_id = ?",
                    (str(pkg_id),),
                ).fetchone()
            finally:
                conn.close()
            if prow is not None:
                sl, tp = _safe_float(prow["sl"]), _safe_float(prow["tp"])
        if not (sl and sl > 0 and tp and tp > 0):
            # Fallback to the symbol+direction protective levels.
            r_sl, r_tp = _resolve_protective_levels(
                db, str(row.get("symbol") or ""), direction)
            sl = sl if (sl and sl > 0) else _safe_float(r_sl)
            tp = tp if (tp and tp > 0) else _safe_float(r_tp)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_classify_broker_exit: level lookup failed for "
                       "trade_id=%s: %s", row.get("id"), exc)
        return None
    if not (sl and sl > 0):
        sl = None
    if not (tp and tp > 0):
        tp = None
    if direction == "long":
        if sl is not None and px <= sl:
            return "sl"
        if tp is not None and px >= tp:
            return "tp"
    else:  # short
        if sl is not None and px >= sl:
            return "sl"
        if tp is not None and px <= tp:
            return "tp"
    return None


def _cascade_close_netted_siblings(
    db,
    primary_row: Dict[str, Any],
    closed_pnl_rec: Optional[Dict[str, Any]],
    *,
    now_iso: Optional[str] = None,
) -> int:
    """Close every OTHER DB-open same-direction trade row on the
    (account, symbol) whose netted position Bybit just reported FLAT.

    BL-20260720-ICTSCALP-PASTSTOP-EXITS: on a netting account several
    journal trades (often from different strategies) share ONE exchange
    position with ONE position-level bracket (the newest trade's). When
    that bracket fires, the whole position flattens — but the reconciler
    closed only the row it happened to be checking. The sibling rows went
    phantom-"open"; a NEW same-symbol position opened by another strategy
    then made every later per-row "position flat?" check read non-flat, so
    the siblings could never close on their own and were eventually
    mis-resolved (other trades' closed-pnl records / stale mark prices —
    the Jun 21–23 2026 bybit_2 BTCUSDT incident).

    The flat observation is the ONE reliable moment we know every share
    closed, so the cascade lives here. Sibling economics are attributed
    honestly from the SAME closed-pnl record that closed the primary:
    ``exit_price`` = the record's ``avg_exit_price`` (correct for every
    share of the flatten), ``pnl`` prorated by the sibling's qty share of
    the record qty (``pnl_source='netted_prorated_cascade'``). With no
    record (fallback path), siblings still close but with the NULL-exit
    honest stamps. Reduce legs close with pnl deferred (BL-20260711
    contract). Siblings created after the record's close time are skipped
    (they belong to a newer position). Best-effort: any failure returns
    the count so far and never blocks the primary close.
    """
    closed = 0
    try:
        acct = str(primary_row.get("account_id") or "")
        symbol = str(primary_row.get("symbol") or "")
        direction = str(primary_row.get("direction") or "")
        primary_id = int(primary_row.get("id"))
        if not acct or not symbol or not direction:
            return 0
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            sibs = conn.execute(
                "SELECT id, account_id, symbol, direction, position_size, "
                "       entry_price, notes, created_at, setup_type "
                "  FROM trades "
                " WHERE status='open' AND COALESCE(is_backtest,0)=0 "
                "   AND account_id=? AND symbol=? AND direction=? AND id != ?",
                (acct, symbol, direction, primary_id),
            ).fetchall()
        finally:
            conn.close()
        if not sibs:
            return 0

        rec = closed_pnl_rec if isinstance(closed_pnl_rec, dict) else None
        avg_exit = _safe_float((rec or {}).get("avg_exit_price")) or 0.0
        rec_pnl = (rec or {}).get("closed_pnl")
        rec_qty = _safe_float((rec or {}).get("qty")) or 0.0
        closed_at = (
            normalize_closed_at_value((rec or {}).get("closed_at"))
            or now_iso
            or datetime.now(timezone.utc).isoformat()
        )
        closed_at_ms = _isoformat_to_ms(closed_at)

        for sib in sibs:
            s = dict(sib)
            # A row created after the flatten belongs to a newer position.
            sib_created_ms = _isoformat_to_ms(s.get("created_at"))
            if (
                closed_at_ms is not None
                and sib_created_ms is not None
                and sib_created_ms > closed_at_ms
            ):
                continue
            notes = _decode_notes(s.get("notes"))
            _setup = str(s.get("setup_type") or "").strip().lower()
            is_reduce = _setup == "intent_reduce" or bool(notes.get("intent_reduce"))
            resolved = None
            if avg_exit > 0:
                resolved = _classify_broker_exit(
                    db, s, avg_exit, is_reduce_leg=is_reduce,
                )
            exit_reason = resolved or "reconciler_filled"
            notes.update({
                "closed_at": closed_at,
                "closed_by": "monitor_reconciler_netted_cascade",
                "closed_reason": (
                    "reconciler — netted position flat; sibling of "
                    f"trade {primary_id} closed by the same position-level "
                    "bracket fire / flatten"
                ),
                "netted_primary_trade_id": primary_id,
                "exit_price_source": (
                    f"{_broker_pnl_source(rec)}_prorated" if avg_exit > 0
                    else "netted_flat_no_record"
                ),
                "exit_reason_source": (
                    "price_vs_pkg_bracket" if resolved else "unresolved"
                ),
            })
            updates: Dict[str, Any] = {
                "status": "closed",
                "exit_reason": exit_reason,
                "closed_at": closed_at,
            }
            if avg_exit > 0:
                updates["exit_price"] = avg_exit
            sib_qty = _safe_float(s.get("position_size")) or 0.0
            if is_reduce:
                notes["pnl_source"] = "deferred_intent_reduce"
            elif rec_pnl is not None and rec_qty > 0 and sib_qty > 0:
                try:
                    # always=True: these rows are siblings of a KNOWN netted
                    # flatten by construction, so the record covers them all —
                    # the ratio test would wrongly skip a sibling whose qty
                    # happens to match the record's.
                    prorated, _ = _prorate_netted_broker_pnl(
                        rec_pnl, rec_qty, sib_qty, always=True,
                    )
                    if prorated is None:
                        raise ValueError("unbookable rec_pnl")
                    updates["pnl"] = round(prorated, 4)
                    notes["pnl_source"] = "netted_prorated_cascade"
                    notes["bybit_closed_pnl_record_total"] = rec_pnl
                    entry = _safe_float(s.get("entry_price"))
                    if entry and entry * sib_qty > 0:
                        updates["pnl_percent"] = round(
                            prorated / (entry * sib_qty) * 100, 4
                        )
                except (TypeError, ValueError):
                    pass
            updates["notes"] = dump_capped(notes, 500)
            try:
                db.update_trade(int(s["id"]), updates)
                _cascade_close_linked_package(
                    db, s.get("id"),
                    close_reason=exit_reason,
                    caller="_cascade_close_netted_siblings",
                )
                closed += 1
                logger.info(
                    "_cascade_close_netted_siblings: closed netted sibling "
                    "trade_id=%s (primary=%s, %s %s %s, exit=%s, "
                    "pnl_source=%s)",
                    s.get("id"), primary_id, acct, symbol, direction,
                    avg_exit if avg_exit > 0 else None,
                    notes.get("pnl_source"),
                )
            except Exception as exc:  # noqa: BLE001 — per-sibling best-effort
                logger.warning(
                    "_cascade_close_netted_siblings: close failed for "
                    "trade_id=%s: %s", s.get("id"), exc,
                )
    except Exception as exc:  # noqa: BLE001 — never block the primary close
        logger.warning(
            "_cascade_close_netted_siblings: cascade aborted for "
            "primary=%s: %s", primary_row.get("id"), exc,
        )
    return closed


def _close_trade_from_order_status(
    db,
    row: Dict[str, Any],
    order_status: Dict[str, Any],
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> tuple:
    """Mark a trade row 'closed' when Bybit reports the entry order
    filled and the position flat. Cascades the linked
    ``order_packages`` row (close_reason='reconciler_filled').

    Exit-price recovery (2026-05-16 follow-up PR): when ``cfg`` is
    available the helper queries Bybit V5
    ``/v5/position/closed-pnl`` via
    :func:`account_closed_pnl_for_trade` and writes the real
    ``avgExitPrice`` as ``exit_price`` on the trade row (plus a
    ``notes.exit_price_source='bybit_closed_pnl'`` stamp + the
    recovered ``closed_pnl`` for posterity). When the lookup
    fails — read error, unsupported category (spot), or no
    matching record — the row still closes (so the
    strategy_monocle gate clears) but with ``exit_price=NULL`` and
    ``notes.exit_price_source='entry_order_avg_price_unreliable'``
    so PnL consumers can filter.

    The ``order_status`` argument carries the entry order's
    ``avg_price``, which is the **entry** fill — emphatically NOT
    the exit fill. Pre-2026-05-16 the helper wrote it as
    ``exit_price`` and produced silently wrong PnL; the previous
    PR (#1268) removed that write. This PR closes the loop by
    sourcing the real exit fill from closed-pnl.

    Args:
      * ``order_status`` — return value of
        :func:`account_order_status`; used for ``exec_time`` (the
        entry fill time, kept as a notes annotation but no longer
        as the closed_at for the trade row when closed-pnl provides
        a real exit time).
      * ``cfg`` — account config dict. Required for the closed-pnl
        recovery; when omitted the helper degrades to the NULL-
        exit-price fallback. Defaulted ``None`` so test fixtures
        that don't care about exit-price recovery keep working.
    """
    notes = _decode_notes(row.get("notes"))

    # Closed-pnl recovery — the real close fill for broker-side-
    # SL/TP closes. Skipped when cfg is missing (legacy callers /
    # tests) or when the account can't supply it (spot category,
    # creds missing, network error). On skip we fall back to the
    # NULL-exit-price contract.
    # BL-20260601-001 prong 2 — intent-reduce / close legs (S-MSE-2)
    # carry a journal ``direction`` + ``entry_price`` that describe the
    # PRIMARY leg's intent, NOT the position actually being reduced: a
    # buy-to-reduce on a held short is journaled direction='long' with
    # the primary leg's intended entry. So the strict closed-pnl side
    # filter (long→Sell) is inverted and the entry±10bps filter points
    # at the wrong price — both mismatch and the realised PnL strands as
    # NULL (verified: live trade #2491, pkg-8596863669584ed5, the only
    # LONG in the 2026-06-08 closed set). For these legs we tell the
    # lookup to match by absolute position movement (qty + close-window)
    # and skip the unreliable direction/entry disambiguators. Gated
    # strictly on the reduce-leg markers so normal trades keep the
    # #1411 / #1419 strict contract.
    _setup_type = str(row.get("setup_type") or "").strip().lower()
    is_reduce_leg = (
        _setup_type == "intent_reduce" or bool(notes.get("intent_reduce"))
    )
    closed_pnl_rec: Optional[Dict[str, Any]] = None
    if cfg is not None:
        try:
            from src.units.accounts.clients import (
                account_closed_pnl_for_trade,
            )
            opened_at_ms = _isoformat_to_ms(row.get("created_at"))
            if opened_at_ms is not None:
                closed_pnl_rec = account_closed_pnl_for_trade(
                    cfg,
                    symbol=str(row.get("symbol") or ""),
                    direction=str(row.get("direction") or ""),
                    opened_at_ms=opened_at_ms,
                    qty=_safe_float(row.get("position_size")),
                    entry_price=_safe_float(row.get("entry_price")),
                    reduce_leg=is_reduce_leg,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_close_trade_from_order_status: closed-pnl lookup raised "
                "for trade_id=%s: %s",
                row.get("id"), exc,
            )
            closed_pnl_rec = None

    if closed_pnl_rec is not None and closed_pnl_rec.get("avg_exit_price"):
        # Real close fill recovered.
        avg_exit_price = float(closed_pnl_rec["avg_exit_price"])
        # Normalise Bybit's epoch-ms close time to ISO before persisting
        # (BL-20260620-RECONCILER-CLOSEDAT-MS) so closed_at stays ISO.
        closed_at = (
            normalize_closed_at_value(closed_pnl_rec.get("closed_at"))
            or datetime.now(timezone.utc).isoformat()
        )
        # Classify the broker close as sl/tp from exit price vs the bracket
        # levels (2026-06-23) — a stop/target fired at the broker before the
        # monitor; surface the TRUE reason instead of the generic reconciler tag.
        # EXCLUDE intent_reduce legs: a reduce is a deliberate partial close (not
        # a bracket hit), and the package bracket can be inverted relative to the
        # reduce-order direction (a long leg reducing a short → sl above / tp
        # below), so classifying it would mislabel a reduce as sl/tp. Reduce legs
        # keep 'reconciler_filled'.
        resolved_reason = _classify_broker_exit(
            db, row, avg_exit_price, is_reduce_leg=is_reduce_leg,
        )
        final_exit_reason = resolved_reason or "reconciler_filled"
        notes.update({
            "closed_at": closed_at,
            "closed_by": "monitor_reconciler",
            "closed_reason":
                "reconciler — Bybit reports order filled and position flat",
            "exit_price_source": _broker_pnl_source(closed_pnl_rec),
            # Close-side fees the venue charged on these fills, stamped HERE
            # because this is the only point they are known — the later local
            # sweep sees only the row, not the fill records.
            **({"close_fees_usd": round(float(closed_pnl_rec["fees"]), 6)}
               if closed_pnl_rec.get("fees") else {}),
            _broker_pnl_note_key(closed_pnl_rec):
                closed_pnl_rec.get("closed_pnl"),
            "finalised_by": "reconciler",
            "exit_reason_source":
                ("price_vs_pkg_bracket" if resolved_reason else "unresolved"),
        })
        updates: Dict[str, Any] = {
            "status": "closed",
            "exit_reason": final_exit_reason,
            "exit_price": avg_exit_price,
            "closed_at": closed_at,
            "notes": dump_capped(notes, 500),
        }
        # 2026-05-19: backfill entry_price from Bybit's entry-order
        # avg_price when available. Pre-fix, `entry_price` was the
        # intent set at order-submit time (see execute.py::
        # _log_trade_to_journal — `pkg.entry` is the strategy's intended
        # entry, NOT the exchange fill). The `execution_quality_labels`
        # dataset's `entry_slippage_bps` computes `actual_entry -
        # intended_entry` from this column joined against
        # `order_packages.entry`, so a never-updated `entry_price`
        # gives a degenerate dataset where mae=0.0 across every row.
        # Updating here only when the order's avg_price differs from
        # what's recorded keeps the write idempotent on re-reconcile.
        _entry_avg_price = _safe_float(order_status.get("avg_price"))
        _entry_current = _safe_float(row.get("entry_price"))
        if _entry_avg_price > 0 and _entry_avg_price != _entry_current:
            updates["entry_price"] = _entry_avg_price
        if is_reduce_leg:
            # BL-20260711: a reduce leg's pnl is DEFERRED — the parent leg
            # carries the realized pnl. On a NETTING account the qty-matched
            # closed_pnl record is the PARENT position's realized close, so
            # booking it onto this bookkeeping leg fabricates a phantom
            # win/loss (the entry==exit +$561/+620/+898 rows). Leave
            # pnl/pnl_percent NULL, matching the
            # apply_intent_reduce_partial_close 'deferred_intent_reduce'
            # contract; _sweep_local_pnl_for_unpriced also skips reduce legs
            # so the universal fallback can't re-fabricate it.
            notes["pnl_source"] = "deferred_intent_reduce"
            updates["notes"] = dump_capped(notes, 500)
        _closed_pnl_val = closed_pnl_rec.get("closed_pnl")
        if _closed_pnl_val is not None and not is_reduce_leg:
            try:
                # Netted-position proration guard
                # (BL-20260720-ICTSCALP-PASTSTOP-EXITS): when the matched
                # closed-pnl record flattened a BIGGER netted position than
                # this row's share (record qty > row qty), booking the full
                # record pnl onto this one row fabricates — the record's
                # economics belong to every journal trade that shared the
                # position. The arithmetic now lives in one helper shared with
                # the cascade and watchdog-recovery paths; the third of those
                # was missing it entirely
                # (BL-20260806-DUPLICATE-PNL-NETTED-SIBLING-ROWS).
                _qty = _safe_float(row.get("position_size")) or 0.0
                _pnl_to_book, _prorated = _prorate_netted_broker_pnl(
                    _closed_pnl_val, closed_pnl_rec.get("qty"), _qty,
                )
                if _pnl_to_book is None:
                    raise ValueError("unbookable closed_pnl")
                if _prorated:
                    notes["pnl_source"] = "netted_prorated"
                    notes["bybit_closed_pnl_record_total"] = _closed_pnl_val
                    updates["notes"] = dump_capped(notes, 500)
                updates["pnl"] = round(_pnl_to_book, 4)
                # Use the post-update entry value for the pnl_percent
                # denominator so the percentage reflects the actual
                # fill, not the stale intent.
                _entry_for_pct = (
                    _entry_avg_price
                    if _entry_avg_price > 0
                    else _entry_current
                )
                if _entry_for_pct and _qty and _entry_for_pct * _qty > 0:
                    updates["pnl_percent"] = round(
                        _pnl_to_book / (_entry_for_pct * _qty) * 100, 4
                    )
            except (TypeError, ValueError):
                pass
    else:
        # Fallback: gate clears but exit_price stays NULL with the
        # unreliable-source flag (pre-2026-05-16 contract preserved
        # for the no-cfg path + the no-record path).
        exec_time = order_status.get("exec_time")
        # exec_time is the ENTRY order's epoch-ms execution time — per this
        # helper's own contract it is an annotation only, never the close
        # time. Stamping it as closed_at backdates the close to the entry
        # fill: trade 3373 (2026-07-13) carried closed_at 72ms BEFORE its
        # created_at while the real position lived two more days, so the
        # row mis-sorted in /trades/closed as an old close and the actual
        # close day showed no BTC close at all. No close-side exec record
        # was recovered on this path, so the honest close time is NOW —
        # the moment the reconciler observed filled+flat
        # (BL-20260713-BYBIT2-BTC-ORPHANS-UNRECONCILED, sibling of
        # BL-20260620-RECONCILER-CLOSEDAT-MS).
        closed_at = datetime.now(timezone.utc).isoformat()
        notes.update({
            "closed_at": closed_at,
            "entry_exec_time": normalize_closed_at_value(exec_time),
            "closed_by": "monitor_reconciler",
            "closed_reason":
                "reconciler — Bybit reports order filled and position flat",
            "exit_price_source": "entry_order_avg_price_unreliable",
        })
        # Fallback path has no recovered exit price, so sl/tp cannot be
        # classified — keep the generic reconciler tag.
        final_exit_reason = "reconciler_filled"
        updates = {
            "status": "closed",
            "exit_reason": "reconciler_filled",
            "closed_at": closed_at,
            "notes": dump_capped(notes, 500),
        }
        # 2026-05-19: same entry_price backfill as the closed_pnl
        # branch above — the entry order's avg_price is still
        # available in the fallback path (only the exit fill is
        # unrecoverable).
        _entry_avg_price = _safe_float(order_status.get("avg_price"))
        _entry_current = _safe_float(row.get("entry_price"))
        if _entry_avg_price > 0 and _entry_avg_price != _entry_current:
            updates["entry_price"] = _entry_avg_price

    # Broker-close mechanism lookup (2026-06-25): when the reconciler can't
    # classify the close as sl/tp (exit price unknown or mid-range), query
    # Bybit's /v5/execution/list to surface the execType of the most recent
    # execution in a 10-minute window. This distinguishes:
    #   "BustTrade"  → demo margin call / liquidation
    #   "AdlTrade"   → auto-deleveraging event
    #   "Trade"      → a normal order filled (manual close / out-of-bracket stop)
    #   None         → no execution found (possible platform reset / data gap)
    # Best-effort — a failure never blocks the close path. The result is both
    # persisted to notes AND returned alongside final_exit_reason so the caller
    # can refine the operator ping classification without a second network round-trip.
    _exec_type: Optional[str] = None
    if final_exit_reason == "reconciler_filled" and cfg is not None:
        try:
            from src.units.accounts.clients import account_exec_type_for_close
            _exec_type = account_exec_type_for_close(
                cfg,
                str(row.get("symbol") or ""),
                end_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            )
            if _exec_type:
                # Re-decode the notes already in updates (already dump_capped),
                # merge the exec_type, re-dump so the DB row carries it.
                _current_notes = _decode_notes(updates.get("notes", "{}"))
                _current_notes["close_exec_type"] = _exec_type
                updates["notes"] = dump_capped(_current_notes, 500)
        except Exception:  # noqa: BLE001
            pass

    db.update_trade(int(row["id"]), updates)

    # Cascade by canonical link (order_packages.linked_trade_id), not
    # by notes-JSON scraping. Pre-2026-05-16 this used
    # ``_extract_package_id(row.notes)`` which silently no-op'd in
    # production because the writer never stamped order_package_id
    # into notes — ``_sweep_stuck_linked_packages`` cleaned up in a
    # second pass with ``close_reason='stuck_cascade_recovered'``,
    # leaving every row with a misleading "recovered" stamp. PR
    # claude/cascade-fix-by-linked-trade-id.
    _cascade_close_linked_package(
        db, row.get("id"),
        close_reason=final_exit_reason,
        caller="_close_trade_from_order_status",
    )
    # Netted-sibling cascade (BL-20260720-ICTSCALP-PASTSTOP-EXITS): the
    # position-flat verdict that closed THIS row means every other open
    # same-direction row on the netted (account, symbol) position closed in
    # the same flatten — close them now with honestly-attributed economics.
    # This is the only moment the flatten is observable: once another
    # strategy re-opens the symbol, per-row position checks read non-flat
    # and the siblings would linger phantom-open (the Jun 21-23 incident).
    _cascade_close_netted_siblings(db, row, closed_pnl_rec)
    return final_exit_reason, _exec_type


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
        "notes": dump_capped(notes, 500),
        # Orphan = red-flag state to resolve, not a silent terminal (item #4).
        "reconcile_status": "unreconciled",
    })
    # Operator directive (2026-06-24): a row entering the orphaned state is a red
    # flag, never an acceptable resting status. Durably log it for the
    # health-review backlog drain + fire the loud "/system-review" red-flag —
    # same guarantee as the reverse-reconciler adopt path. Best-effort.
    try:
        from src.runtime.execution_diagnostics import enqueue_orphan_created_flag
        enqueue_orphan_created_flag(
            account=row.get("account_id"),
            symbol=row.get("symbol"),
            side=row.get("direction"),
            trade_id=int(row["id"]),
            origin="forward_reconciler_orphaned",
            reason=("DB-open trade absent from the exchange open-positions "
                    "snapshot — marked orphaned by the monitor reconciler"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_mark_orphaned: orphan-created flag failed for trade_id=%s: %s",
            row.get("id"), exc,
        )
    # Cascade by canonical link (order_packages.linked_trade_id),
    # with a second attempt on transient failures. Pre-2026-05-16
    # the lookup went through ``_extract_package_id(row.notes)``,
    # which the live writer doesn't populate; the orphan cascade
    # was silently dead in production and ``_sweep_stuck_linked_packages``
    # was doing all the work. PR claude/cascade-fix-by-linked-trade-id.
    pkg_id = _resolve_linked_package_id(db, row.get("id"))
    if not pkg_id:
        return

    last_exc: Optional[BaseException] = None
    affected = 0
    for attempt in (1, 2):
        try:
            affected = db.update_order_package(pkg_id, {
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
    elif not affected:
        # Lookup succeeded but the UPDATE matched zero rows — stale
        # link. Log audibly so the operator notices; the sweep will
        # not catch this case because the package id is "real" from
        # the linked_trade_id query but the row may have been deleted.
        logger.warning(
            "_mark_orphaned: package cascade no-op for pkg_id=%s linked to "
            "trade_id=%s (row not found — stale link?)",
            pkg_id, row.get("id"),
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
    empty dict on missing / malformed content.

    Delegates to ``src.utils.json_notes.load_notes`` — the symmetric half of
    ``dump_capped``, which this module already uses for the write side. Kept as
    a named local so the many call sites here read unchanged; the BEHAVIOUR is
    now defined in exactly one place, so the pairs close (M39 track B) and this
    path cannot drift into two answers about what a malformed blob decodes to.
    """
    return _load_notes(notes_raw)


_NAKED_POSITION_GRACE_SECONDS = 300  # 5 min after opening before alerting

# Bybit conditional-order types that constitute a STOP (protection). Mirrors
# execute.py's ``_SL_LEG_TYPES`` — a divergence here would misgrade coverage.
_SL_LEG_TYPES_MON = {"stoploss", "partialstoploss"}
#: The TAKE-PROFIT half. Used ONLY to count legs against Bybit's
#: 20-COMBINED-leg-per-symbol cap — never for stop coverage, which is an
#: SL-only question and must stay one.
_TP_LEG_TYPES_MON = {"takeprofit", "partialtakeprofit"}

# Tolerance when comparing summed protective-leg qty against the netted IB
# position size (BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY). Futures trade
# in whole contracts, so anything below one contract is float noise, never a
# genuine uncovered remainder — without it a 3.0000000000000004 vs 3.0 compare
# would report a permanently partially-naked position and re-arm every sweep.
_IB_COVERAGE_EPSILON = 0.5

# Fractional slack when comparing summed leg qty against position size. Bybit
# echoes leg qty as a string at the instrument's qty step, so a hair of float
# noise must not be read as a coverage hole. 0.5% of position size.
_BYBIT_COVERAGE_EPS_FRAC = 0.005

# Resting SL legs totalling more than this multiple of the position size means
# legs have ACCUMULATED rather than tracking the position (the BL-20260721
# 20-leg-cap failure mode). 1.5× tolerates one legitimately-stale leg mid-amend;
# the live case that motivated it was 4.4× (bybit_1 XRPUSDT, 2026-07-30).
_BYBIT_OVERCOVER_FACTOR = 1.5

#: IB's sibling of the factor above (BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS).
#: The class was implemented for Bybit and never ported: `_check_broker_naked_ib_positions`
#: asked only whether coverage was SUFFICIENT, so 30 contracts of stop against a 15 long
#: passed with room to spare. Same default as Bybit so the two venues are not silently
#: graded on different thresholds.
_IB_OVERCOVER_FACTOR = 1.5

# Fractional tolerance before open-journal-qty EXCEEDING the netted exchange
# size is reported as a phantom-row divergence. Generous (10%) so ordinary
# rounding / a mid-fill race never cries wolf; the live cases were +38% and
# +15,000%.
_BYBIT_QTY_DIVERGENCE_FRAC = 0.10

# Cadence gate for the IB broker-side naked sweep
# (:func:`_check_broker_naked_ib_positions`, BL-20260709-IB-BROKER-PROTECTION-
# UNVERIFIED). Build constraint 1: an account-wide IB order read
# (``reqAllOpenOrders``) on EVERY monitor tick risks the tick-latency / IB
# pacing wedge class (BL-20260609), so this sweep runs at most once per
# interval — NOT each tick. The Alpaca equity sweep needs no such gate (cheap
# HTTP). Default 300s (≈ every 5th 60s monitor tick); ``<= 0`` disables the
# sweep entirely. In-process monotonic latch, reset on a fresh restart.
_DEFAULT_IB_BROKER_NAKED_CHECK_SECONDS = 300
_LAST_IB_BROKER_NAKED_CHECK_MONO: float = 0.0

# Rate-limit the target-naked CRITICAL per (account, symbol). The sweep runs
# on IB_BROKER_NAKED_CHECK_SECONDS (default 300s) and the condition PERSISTS
# until someone repairs the bracket, so an un-limited alert would fire every
# five minutes forever — which is the desensitized-alarm P1 this repo treats
# as its own bug, not diligence. One page per 6h per (account, symbol) keeps
# it loud without training the operator to scroll past it.
#
# THAT INTENT WAS RIGHT AND THE FIRST IMPLEMENTATION COULD NOT DELIVER IT
# (fixed 2026-08-23): the latch was a module global keyed on time.monotonic(),
# both per-PROCESS, and the trader restarts on every merge to `main` -- so the
# cooldown reset on every restart and the alert became 53.7% of the entire
# ERROR+/CRITICAL feed. The latch is durable now; see
# tests/test_target_naked_cooldown_is_durable.py for the measurement.
_TARGET_NAKED_ALERT_COOLDOWN_S: float = 6 * 3600.0
_TARGET_NAKED_STATE_FILENAME = "target_naked_alert_state.json"
# Entries older than this are dropped on write so the file cannot grow without
# bound; well past the cooldown, so pruning can never un-suppress a live one.
_TARGET_NAKED_STATE_TTL_S: float = 7 * 24 * 3600.0


# ── The durable alert cooldown, GENERALISED (2026-08-25) ─────────────────────
# The three functions below were written for `target_naked` alone. A SECOND
# safety page then needed exactly the same behaviour (`stop_over_cover`), and
# copy-pasting the latch is how the two drift: the bug the target-naked latch
# was FIXED for on 2026-08-23 -- a per-PROCESS `time.monotonic()` key on a
# condition that outlives any process -- is precisely the bug a copy would
# reintroduce, silently, in the copy. So the mechanism is parameterised by an
# alert KIND and both callers share one implementation.
#
# The filename is `<kind>_alert_state.json`, which reproduces
# `target_naked_alert_state.json` byte-for-byte for the original caller. That
# is deliberate and load-bearing: renaming it would orphan the LIVE latch file
# on the trader and silently re-arm a cooldown that is currently suppressing.


def _alert_state_path(kind: str):
    """``runtime_logs/<kind>_alert_state.json``.

    Kept HERE, and kept module-level, on purpose: it is the seam the durable-
    cooldown tests patch, and the live trader holds
    ``target_naked_alert_state.json`` under exactly this name. The logic below
    lives in :mod:`src.runtime.alert_cooldown` — see that module for why it is
    shared rather than copied — and every wrapper threads this resolver back in
    so patching it still works.
    """
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / f"{kind}_alert_state.json"


def _resolver(kind: str):
    # Late-bound on purpose: resolving `_alert_state_path` at CALL time is what
    # keeps `monkeypatch.setattr(om, "_alert_state_path", ...)` effective.
    return _alert_state_path(kind)


def _load_alert_state(kind: str) -> tuple:
    return _alert_cooldown.load_state(kind, _resolver)


def _save_alert_state(kind: str, state: dict) -> None:
    _alert_cooldown.save_state(kind, state, _resolver)


_SEV_SUFFIX = _alert_cooldown.SEV_SUFFIX


def _cooldown_admits(kind: str, key: str, cooldown_s: float,
                     ttl_s: float = _TARGET_NAKED_STATE_TTL_S,
                     severity: Optional[int] = None) -> bool:
    """Should ``key`` alert now under ``kind``'s durable cooldown?

    Thin wrapper over :func:`src.runtime.alert_cooldown.cooldown_admits`, which
    holds the behaviour and the reasoning (wall-clock not monotonic; a
    WORSENING `severity` breaks the window while an equal or improving one does
    not; an unreadable latch alerts rather than suppressing).
    """
    return _alert_cooldown.cooldown_admits(
        kind, key, cooldown_s, ttl_s=ttl_s, severity=severity,
        path_resolver=_resolver,
    )


# Back-compat aliases. `_TARGET_NAKED_STATE_FILENAME` still names the file the
# live trader holds, and the existing durable-cooldown tests bind these names.
def _target_naked_state_path():
    return _alert_state_path("target_naked")


def _load_target_naked_state() -> tuple:
    return _load_alert_state("target_naked")


def _save_target_naked_state(state: dict) -> None:
    _save_alert_state("target_naked", state)


def _emit_target_naked_alert(
    *,
    account_id: str,
    symbol: str,
    size: float,
    target_qty: float,
    stop_qty: Any,
    declared_tp: Any,
    trade_id: Any,
    venue: str = "ib",
) -> bool:
    """Page the operator that a position has a declared TP and no target at the
    broker. Rate-limited per (account, symbol). Returns whether it alerted.

    CRITICAL, not WARN: a position that can only stop out or run is the state
    the operator has ruled unacceptable, and CRITICAL is what reaches Telegram
    (``outcomes._TELEGRAM_LEVELS``) as well as the ``/api/bot/notifications``
    banner. Never raises into the sweep.
    """
    # SEVERITY IS THE UNCOVERED QUANTITY — criterion 4 of
    # BL-20260825-OVER-COVER-LATCH-CANNOT-SEE-A-WORSENING-CONDITION; this latch
    # shares the shape the over-cover page was fixed for.
    # A position whose target coverage DROPS is
    # a new fact and pages; one whose coverage merely persists, or IMPROVES, is
    # suppressed for the window. That second half is why this uses the shared
    # one-directional primitive rather than folding the number into the key:
    # target coverage moves in both directions, and paging CRITICAL on a
    # position getting BETTER protected is the desensitized-alarm P1 itself.
    try:
        shortfall = max(0, int(round(float(size) - float(target_qty))))
    except (TypeError, ValueError):
        shortfall = None  # ungradeable — fall back to the plain per-key latch
    if not _cooldown_admits(
        "target_naked", f"{account_id}|{symbol}", _TARGET_NAKED_ALERT_COOLDOWN_S,
        severity=shortfall,
    ):
        return False
    try:
        from src.runtime.outcomes import Level, report

        report(
            # Venue-scoped, because an Alpaca condition reported under an
            # `ib_` event name is the same mislabel class this whole fix is
            # about. `venue="ib"` preserves the original name exactly.
            f"{venue}_target_naked",
            "detected",
            level=Level.CRITICAL,
            reason=(
                f"{account_id}/{symbol}: position {size} has {target_qty} of "
                f"take-profit coverage against a declared TP of {declared_tp} "
                f"— the position can only stop out or run"
            ),
            account_id=account_id,
            symbol=symbol,
            size=size,
            target_qty=target_qty,
            stop_qty=stop_qty,
            declared_tp=declared_tp,
            trade_id=trade_id,
        )
    except Exception:  # noqa: BLE001 — an alert failure must never abort the sweep
        logger.exception(
            "_emit_target_naked_alert: alert failed for %s/%s", account_id, symbol
        )
    return True


# Cooldown for the disjoint-OCA stop-over-cover page. Same 6h/(account,symbol)
# shape as the target-naked page and for the same reason: the condition
# PERSISTS until a human repairs the bracket, so an un-limited alert fires
# every sweep forever and becomes the thing the operator scrolls past.
_STOP_OVER_COVER_ALERT_COOLDOWN_S: float = 6 * 3600.0


def _classify_group_owner(
    group_client_id: Optional[int], reader_client_id: Optional[int]
) -> str:
    """Can THIS session cancel the group submitted under *group_client_id*?

    Three states, never collapsed
    (BL-20260825-OVER-COVER-PAGE-CANNOT-SAY-WHY-THE-GROUPS-ARE-DISJOINT --
    kept on one line so the id stays greppable):

    - ``this_session``  -- the ids match, so a cancel from here is addressed
      correctly. This is the group the operator CAN act on.
    - ``other_session`` -- a different id. IB binds cancel rights to the
      submitting clientId, so this group cannot be cancelled from here **at
      all**, whether that session is a live sibling (the 496/497/498 exec
      cluster, a readonly diag client) or has retired. Deliberately NOT called
      "retired": this function cannot tell those apart, and asserting the
      stronger one would be a claim nothing measured.
    - ``unknown``       -- an id was unreadable. That is *we could not look*,
      and it is emphatically not ``this_session``; treating a missing id as
      ours is how a page would promise an action that then fails.

    NOT registered with ``collapsed-state-guard``, deliberately, and this is the
    same call the ``BYBIT_HEDGE_MODE_SYMBOLS`` states record. The guard excludes
    a contract's producer file from its own consumer set, and the only code that
    branches on these states IS the producer module -- so a contract row today
    would be satisfied by the test file alone, or would invite a decorative
    branch elsewhere to feed it. Both are worse than no row. It becomes
    registrable in the change that first gives another module a real reason to
    branch on the owner (a remediation path, a soak row, an API field). The
    collapse that matters -- ``unknown`` read as ``this_session`` -- is pinned
    directly in tests/test_stop_over_cover_alert.py meanwhile.
    """
    if group_client_id is None or reader_client_id is None:
        return "unknown"
    return "this_session" if group_client_id == reader_client_id else "other_session"


def _emit_stop_over_cover_alert(
    *,
    account_id: str,
    symbol: str,
    size: float,
    stop_qty: float,
    oca_groups: Any,
    group_client_ids: Optional[Mapping[str, Optional[int]]] = None,
    reader_client_id: Optional[int] = None,
    venue: str = "ib",
) -> bool:
    """Page the operator that resting stops EXCEED the position across DISJOINT
    OCA groups. Rate-limited per (account, symbol). Returns whether it alerted.

    WHY THIS EXISTS (2026-08-25). The detection has been correct since
    BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS: the sweep counts
    `over_covered` and logs a `logger.error`. But `logger.error` writes to the
    journal and NOTHING ELSE — it does not reach `outcomes.jsonl`, which is
    what feeds Telegram, the `/api/bot/notifications` banner and
    `/api/bot/logs?level=error`. Measured live this session on a 1000-row
    ERROR+ feed spanning 2026-08-20T07:01Z–2026-08-25T09:28Z (388 rows):
    **ZERO** rows mention over-cover, while the venue read
    (`/api/diag/ib_open_orders?account_id=ib_paper`, same session) shows
    ib_paper MHG holding a 29-lot position against **two disjoint OCA groups**
    (`oca-protect-416`, `oca-protect-432`) each carrying a 29-lot STP and a
    29-lot LMT — 58 of stop against 29 of position, **200%**. So the condition
    was live, detected, and invisible on every surface a human reads.

    WHY IT IS CRITICAL. OCA cancels only WITHIN a group. If 416's stop fires it
    flattens the position and 432's legs are STILL RESTING, so the next trigger
    sells another 29 into a **naked short** — an unhedged position in the
    opposite direction that nothing placed deliberately. That is a
    money-at-risk state, not a hygiene issue, and CRITICAL is the level that
    reaches Telegram (`outcomes._TELEGRAM_LEVELS`).

    DETECT-ONLY, deliberately. It pages; it cancels nothing. The one attempt to
    remediate this class automatically cancelled the leg that MATCHED the
    journal, which is worse than the divergence — see
    BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG.
    Choosing which leg dies is the operator's call.

    WHY IT NAMES THE SUBMITTING clientId (2026-08-25,
    BL-20260825-OVER-COVER-PAGE-CANNOT-SAY-WHY-THE-GROUPS-ARE-DISJOINT). The
    instruction above — *cancel the leg that does not match trades.stop_loss* —
    was **un-executable for the leg it names**, and the page gave the operator
    no way to know that. IB binds cancel rights to the SUBMITTING clientId, so
    a group placed by a session that has since rotated its id cannot be
    cancelled from the trader at all; it has to be cleared in TWS. Measured
    live on ib_paper MHG the same day: the stale group `oca-protect-446` (STP
    6.2625 — the PREVIOUS trail level) sits on clientId 597 while the live
    group `oca-protect-465` (STP 6.312, matching `trades.stop_loss`
    6.31207143) sits on 497.

    That measurement also names the CAUSE, which no field previously carried:
    the second group is not a duplicate of unknown origin, it is the trailing
    amend's own cancel-and-re-place with the **cancel half refused** (Error
    10147). So the group count grows by one per (clientId rotation, trailing
    amend) pair, and a page that says only "2 disjoint groups" describes a
    symptom whose mechanism is one field away.

    Never raises into the sweep.
    """
    groups = sorted(oca_groups or [])
    # SEVERITY IS THE DISJOINT-GROUP COUNT, so a worsening breaks the cooldown
    # (BL-20260825-OVER-COVER-LATCH-CANNOT-SEE-A-WORSENING-CONDITION). Keyed on
    # (account, symbol) alone, this page fired once on ib_paper/MHG at
    # 2026-08-25T12:27:44Z for 2 groups / 200% and then said nothing while the
    # SAME symbol reached 3 groups / 300% two hours later, because the 6h
    # cooldown had not elapsed.
    #
    # The group count is the right severity here rather than the percentage:
    # it is what makes the naked-reverse hazard worse (one MORE bracket that a
    # firing stop cannot cancel), it is an integer so it cannot flap, and it
    # only grows under the producing defect. The 6h window itself stays, and
    # must — it is the direct remedy for
    # BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART, where 202 of
    # 376 CRITICAL rows (53.7% of the whole operator ERROR+ feed) were one
    # un-latched alarm. An UNCHANGED count is still one page per 6h.
    if not _cooldown_admits(
        "stop_over_cover",
        f"{account_id}|{symbol}",
        _STOP_OVER_COVER_ALERT_COOLDOWN_S,
        severity=len(groups),
    ):
        return False
    try:
        from src.runtime.outcomes import Level, report

        pct = (100.0 * float(stop_qty) / float(size)) if size else None
        ids = dict(group_client_ids or {})
        owners = {
            g: _classify_group_owner(ids.get(g), reader_client_id)
            for g in groups
        }
        # Render as `group(clientId=X, other_session)` so the operator reads the
        # address and the consequence together. A group we cannot cancel from
        # here is the actionable half of the page, so say it in words rather
        # than leaving the reader to compare two ids.
        rendered = ", ".join(
            f"{g}(clientId={ids.get(g) if ids.get(g) is not None else '?'}, "
            f"{owners[g]})"
            for g in groups
        )
        foreign = [g for g in groups if owners[g] == "other_session"]
        unknown_owner = [g for g in groups if owners[g] == "unknown"]
        advice = (
            "Detect-only: cancel the leg that does NOT match trades.stop_loss."
        )
        if foreign:
            advice += (
                f" NOTE {len(foreign)} group(s) {foreign} were submitted by a "
                f"DIFFERENT clientId than this session ({reader_client_id}), so "
                "the trader cannot cancel them itself — IB binds cancel rights "
                "to the submitter and refuses a foreign cancel (Error 10147). "
                "Use the `cancel-ib-order` system-action, which reads the "
                "owning clientId account-wide and CONNECTS AS IT: one issue per "
                "order, `force_protective: true` + `force_client_id: true`, "
                "DRY-RUN first. ⚠️ An owning id below 9000 is in the trader's "
                "execution band and connecting as it EVICTS the trader's live "
                "IB session — that is what the second override is asking about, "
                "so read the dry run before waiving it. "
                "`scripts/ops/over_cover_proposal.py` emits the issue bodies "
                "with the group selection already made. This is also the likely "
                "CAUSE of the split: a trailing amend cancel-and-re-places, and "
                "when the cancel half is refused the re-place leaves a new "
                "disjoint group behind."
            )
        if unknown_owner:
            advice += (
                f" {len(unknown_owner)} group(s) {unknown_owner} reported no "
                "clientId — that is unreadable, not this session; confirm the "
                "owner before attempting a cancel."
            )
        report(
            f"{venue}_stop_over_cover",
            "detected",
            level=Level.CRITICAL,
            reason=(
                f"{account_id}/{symbol}: position {size} but resting STOP qty "
                f"totals {stop_qty}"
                + (f" ({pct:.0f}%)" if pct is not None else "")
                + f" across {len(groups)} DISJOINT OCA groups "
                + (rendered or str(groups))
                + ". OCA cancels only within a group, so one stop firing "
                "flattens the position and leaves the other(s) resting to sell "
                "again into a naked SHORT. " + advice
            ),
            account_id=account_id,
            symbol=symbol,
            size=size,
            stop_qty=stop_qty,
            oca_groups=groups,
            over_cover_pct=pct,
            # Structured beside the prose so a consumer can branch without
            # parsing the reason string.
            oca_group_client_ids=ids,
            reader_client_id=reader_client_id,
            group_owners=owners,
        )
    except Exception:  # noqa: BLE001 — an alert failure must never abort the sweep
        logger.exception(
            "_emit_stop_over_cover_alert: alert failed for %s/%s",
            account_id, symbol,
        )
    return True


#: The backlog row for the selector defect the remedy advice below must carry.
#: Held whole in ONE constant rather than split across a string concatenation:
#: a wrapped id reads as a DANGLING reference to any grep of the source (and to
#: `artifact-validity-guard`, which caught exactly that here), even though the
#: concatenation joins fine at runtime.
_STALE_LEG_SELECTOR_BACKLOG_ID = (
    "BL-20260826-CANCEL-STALE-TPSL-LEGS-WOULD-KEEP-A-CLOSED-TRADES-LEG-AND-CANCEL-THE-LIVE-ONE"
)

#: Bybit's combined TP+SL leg cap per symbol. Proximity to it is the actionable
#: urgency on an over-accumulated symbol: once it is reached, `set_trading_stop`
#: refuses (ErrCode 110061) and a GENUINE protective tightening silently fails
#: (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP).
_BYBIT_LEG_CAP = 20


def _bybit_over_cover_condition(
    *,
    size: float,
    covered: float,
    leg_side_split: Optional[Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """Name WHICH condition tripped the over-cover check, and carry its numbers.

    Returns ``(prose, structured_fields)``. This is the branch the 2026-09-02
    fix exists to add: the side-blind sum that trips the check is the UNION of
    two conditions with DIFFERENT remedies, and the page used to describe only
    one of them.

    * legs that REDUCE THE GRADED BOOK exceeding the position → same-side stale
      legs have piled up. That is the original ``over_covered`` condition and
      the one ``cancel-stale-tpsl-legs`` is for.
    * legs that reduce the OTHER book → protection acting on a book we did not
      grade. Whether that book exists is :func:`bybit_leg_sides.other_book_state`'s
      question, not this one's, and the prose says which of its three answers
      applies rather than asserting "orphaned".

    Both can hold at once; both are then reported. ⚠️ NEITHER may be reported
    as absent when we could not look: an unreadable leg side or an unreadable
    POSITION side is stated outright, because a sum over a population we could
    only partly classify is a lower bound and reads exactly like a clean one.
    """
    if not leg_side_split:
        # `_bybit_position_protection` returns None here only on the Full-mode
        # branch, which never reaches this page. Say WE DID NOT LOOK rather
        # than render a clean-looking zero split.
        return (
            "The per-book split of the resting legs was NOT computed, so which "
            "book they act on is UNKNOWN — this is not a finding that they all "
            "protect the graded position.",
            {"leg_side_split_state": "not_computed"},
        )

    graded_q = float(leg_side_split.get(
        f"{_bybit_leg_sides.LEG_REDUCES_GRADED_BOOK}_qty") or 0.0)
    graded_n = int(leg_side_split.get(
        f"{_bybit_leg_sides.LEG_REDUCES_GRADED_BOOK}_legs") or 0)
    other_q = float(leg_side_split.get(
        f"{_bybit_leg_sides.LEG_REDUCES_OTHER_BOOK}_qty") or 0.0)
    other_n = int(leg_side_split.get(
        f"{_bybit_leg_sides.LEG_REDUCES_OTHER_BOOK}_legs") or 0)
    leg_unread_n = int(leg_side_split.get(
        f"{_bybit_leg_sides.LEG_SIDE_UNREADABLE}_legs") or 0)
    pos_unread_n = int(leg_side_split.get(
        f"{_bybit_leg_sides.POSITION_SIDE_UNREADABLE}_legs") or 0)
    qty_unread_n = int(leg_side_split.get("qty_unreadable_legs") or 0)
    book_state = str(leg_side_split.get("other_book_state") or
                     _bybit_leg_sides.OTHER_BOOK_UNKNOWN)

    parts: List[str] = []

    # --- the graded book -----------------------------------------------------
    if pos_unread_n:
        # POSITION_SIDE_UNREADABLE: no leg could be classified at all.
        parts.append(
            f"The position's own SIDE was unreadable, so NONE of the "
            f"{pos_unread_n} resting leg(s) could be attributed to a book. We "
            "could not look — do not read this as over-protection of the live "
            "position."
        )
    else:
        graded_pct = (100.0 * graded_q / size) if size else None
        over_same_book = graded_q > size * _BYBIT_OVERCOVER_FACTOR
        if over_same_book:
            parts.append(
                f"SAME-BOOK LEG OVER-ACCUMULATION: legs that REDUCE THIS "
                f"position total {graded_q} across {graded_n} leg(s)"
                + (f" ({graded_pct:.0f}% of the position)"
                   if graded_pct is not None else "")
                + " — stale same-side legs have piled up; a trip would "
                "over-close and strand the rest."
            )
        else:
            parts.append(
                f"THIS position is NOT over-protected: legs that reduce it "
                f"total {graded_q} across {graded_n} leg(s)"
                + (f" ({graded_pct:.0f}% of the position)"
                   if graded_pct is not None else "")
                + "."
            )

    # --- the other book ------------------------------------------------------
    if other_n:
        if book_state == _bybit_leg_sides.OTHER_BOOK_IMPOSSIBLE_ONE_WAY:
            tail = (
                "The venue reports positionIdx=0 (ONE-WAY netting), so no "
                "opposite book can exist: these are STRANDED legs of a "
                "position that is gone."
            )
        elif book_state == _bybit_leg_sides.OTHER_BOOK_POSSIBLE_HEDGE:
            tail = (
                "The venue reports a HEDGE book (positionIdx 1/2), so these "
                "MAY be a LIVE sibling position's protection — read "
                "/api/diag/bybit_open_orders and the journal before treating "
                "them as stranded. DO NOT cancel them on this page alone."
            )
        else:
            tail = (
                "The venue did not report a readable positionIdx, so whether "
                "an opposite book exists is UNKNOWN — we could not look, which "
                "is NOT 'the book is flat'."
            )
        parts.append(
            f"SEPARATELY, {other_n} leg(s) totalling {other_q} rest on the "
            f"side that reduces the OPPOSITE book — they cannot protect this "
            f"position at all. {tail}"
        )

    # --- what we could not grade --------------------------------------------
    if leg_unread_n:
        parts.append(
            f"{leg_unread_n} leg(s) carried no readable side and are in "
            "NEITHER figure above — both are lower bounds."
        )
    if qty_unread_n:
        parts.append(
            f"{qty_unread_n} leg(s) carried no readable qty and contribute 0 "
            "to the sums above, which are therefore lower bounds."
        )

    fields = {
        "leg_side_split_state": "computed",
        "graded_book_qty": graded_q,
        "graded_book_legs": graded_n,
        "other_book_qty": other_q,
        "other_book_legs": other_n,
        "leg_side_unreadable_legs": leg_unread_n,
        "position_side_unreadable_legs": pos_unread_n,
        "leg_qty_unreadable_legs": qty_unread_n,
        "other_book_state": book_state,
    }
    return " ".join(parts), fields


def _emit_bybit_over_cover_alert(
    *,
    account_id: str,
    symbol: str,
    size: float,
    covered: float,
    leg_count: int,
    protective_leg_count: Optional[int] = None,
    leg_side_split: Optional[Dict[str, Any]] = None,
) -> bool:
    """Page the operator that resting Bybit SL legs EXCEED the position.
    Rate-limited per (account, symbol). Returns whether it alerted.

    WHY THIS EXISTS (2026-08-26). The Bybit sweep has counted `over_covered`
    since BL-20260730-BYBIT1-XRP-LEG-OVERACCUM-WORSENING and reports it with a
    `logger.error` — which reaches the systemd journal and NOTHING ELSE. It
    never writes `outcomes.jsonl`, which is what feeds Telegram, the
    `/api/bot/notifications` banner and `/api/bot/logs?level=error`. This is the
    same defect fixed for IB one day earlier (`_emit_stop_over_cover_alert`),
    and that fix explicitly left Bybit unchecked.

    MEASURED with a POSITIVE CONTROL, because a quiet feed proves nothing on its
    own. Over the 401-row operator ERROR+ feed spanning
    2026-08-20T09:42Z-2026-08-26T00:33Z: three `ib_stop_over_cover` rows (the IB
    page, firing correctly on ib_paper/MHG) and **ZERO** from Bybit — while the
    trader's own symbol-scoped read had `bybit_1`/ETHUSDT at 167% over-covered.
    So the probe finds a positive; Bybit simply never had a page to find.

    ⚠️ THIS IS DELIBERATELY *NOT* `_emit_stop_over_cover_alert(venue="bybit")`.
    That function's entire hazard argument is IB-specific and would be FALSE
    here: it warns that one stop firing leaves a disjoint OCA group resting to
    sell again into a **naked SHORT**, and it routes the operator to
    `cancel-ib-order` over IB's clientId cancel-rights rule (Error 10147).
    Bybit has no OCA groups, and every resting Bybit SL leg is `reduceOnly`
    (measured on all four live `bybit_1` symbols, 2026-08-26) so it CANNOT flip
    the position. Reusing the IB page would be a semantic substitution: a
    confident label over a quantity the code did not compute.

    THE BYBIT HAZARD, stated for what it is:

    1. **The 20-leg cap.** Legs pile up until `set_trading_stop` refuses, at
       which point a genuine protective tightening fails *silently* — the stop
       the strategy believes it moved never moved. That is the money-at-risk
       half, and it is why `leg_count` is on the page.
    2. **A dead trade's leg cutting a live position.** Reduce-only legs owned by
       CLOSED rows still trigger, so a live position gets cut at a level chosen
       by a trade that no longer exists.

    ⚠️ **THE HEADLINE USED TO NAME A CAUSE NO CODE PATH TESTED** (fixed
    2026-09-02). It read "position 0.018 but resting SL legs total 0.478
    (2656%)", built from a SIDE-BLIND sum, which invites the reader to
    investigate why the LIVE position is over-protected. MEASURED on
    ``bybit_1``/BTCUSDT (``/api/diag/bybit_open_orders``, read
    2026-09-02T03:30:33Z, trader ``git_sha 68e73de8``): the live position was
    ``Buy 0.018 positionIdx=1`` and its own legs were ``Sell 0.018`` SL +
    ``Sell 0.018`` TP — an exact 1.00x match, NOT over-protected. The whole
    excess was ``Buy 0.46`` SL + ``Buy 0.46`` TP, reduce-only orders that can
    only reduce a SHORT. Different condition, different remedy. That is
    UNPROVENANCED DIAGNOSTIC OUTPUT sub-class A (``CLAUDE.md`` § "Diagnostic
    provenance"), and the fix is to BRANCH ON THE ACTUAL CONDITION rather than
    reword the label — hence ``leg_side_split``
    (:mod:`src.runtime.bybit_leg_sides`).

    ⚠️ **THE TRIGGER STAYS SIDE-BLIND, DELIBERATELY.** The caller still trips on
    the side-blind sum, because that sum is the UNION of both conditions:
    narrowing the trigger to same-book coverage would make the orphan case go
    SILENT, which is strictly worse than mislabelling it. What changed is only
    what the page SAYS once it has fired.

    ⚠️ **AND THE PAGE STILL DOES NOT ASSERT "ORPHANED".** Under one-way netting
    an other-book leg is stranded by construction; under HEDGE mode — armed on
    this account since 2026-08-30 — it may be a LIVE sibling's protection.
    ``other_book_state`` carries which, including ``unknown`` for *we could not
    look*. Saying "orphaned" without establishing the sibling book is empty
    would re-commit the exact error being fixed.

    LEVEL IS `ERROR`, NOT `CRITICAL`, and that is a judgement worth stating.
    Both reach Telegram (`outcomes._TELEGRAM_LEVELS` = {ERROR, CRITICAL}), so
    nothing is lost in delivery. CRITICAL is reserved here for a position that
    is UNPROTECTED or REVERSED; this one is over-protected and reduce-only.
    Spending CRITICAL on it is how the channel gets trained away — 202 of 376
    CRITICAL rows were once a single un-latched alarm
    (BL-20260823-TARGET-NAKED-COOLDOWN-RESETS-ON-EVERY-RESTART), which is the
    desensitized-alarm P1 this repo names.

    SEVERITY IS THE LEG COUNT, so a WORSENING breaks the 6h cooldown while a
    steady or improving one does not — the same reasoning as the IB page's
    group count: it is an integer (cannot flap), it only grows under the
    producing defect, and it is what moves the symbol toward the cap.

    ⚠️ THE COOLDOWN IS THE SHARED `_cooldown_admits`, NEVER A COPY. Copy-pasting
    a latch is exactly how the per-PROCESS `time.monotonic()` defect that put
    202 CRITICALs on the operator's channel would return in the copy.

    Never raises into the sweep.
    """
    if not _cooldown_admits(
        "bybit_over_cover",
        f"{account_id}|{symbol}",
        _STOP_OVER_COVER_ALERT_COOLDOWN_S,
        # Severity tracks the COMBINED count when we have it: that is the
        # quantity that worsens toward the cap. Falls back to the stop count
        # rather than to nothing, so an uncountable target side never silences
        # a worsening.
        severity=int(protective_leg_count
                     if protective_leg_count is not None else (leg_count or 0)),
    ):
        return False
    try:
        from src.runtime.outcomes import Level, report

        pct = (100.0 * float(covered) / float(size)) if size else None
        # ⚠️ THE CAP IS 20 COMBINED TP+SL LEGS, so the headroom must be computed
        # from the COMBINED count — never from `leg_count`, which is SL-only.
        # Fixed 2026-08-26, hours after this page shipped: it reported
        # "9 of 20 used, 11 left" on bybit_1/ETHUSDT while the venue actually
        # held 9 SL + 9 TP = 18 of 20, TWO left. A cap-proximity alarm that
        # overstates headroom by 9 legs is worse than none, because it reads as
        # a measurement.
        combined = (int(protective_leg_count)
                    if protective_leg_count is not None else None)
        if combined is not None:
            headroom = _BYBIT_LEG_CAP - combined
            cap_note = (
                f" {combined} of Bybit's {_BYBIT_LEG_CAP}-leg COMBINED TP+SL "
                f"cap are used ({headroom} left; {leg_count} of them are stop "
                "legs); at the cap `set_trading_stop` refuses (ErrCode 110061) "
                "and a genuine protective tightening fails SILENTLY."
            )
        else:
            # We could not count the target legs. Say so — do NOT publish a
            # headroom derived from the stop count alone, which is exactly the
            # substitution this branch exists to stop.
            headroom = None
            cap_note = (
                f" {leg_count} STOP leg(s) rest; the target legs were not "
                f"counted, so headroom against Bybit's {_BYBIT_LEG_CAP}-leg "
                "COMBINED TP+SL cap is UNKNOWN (not large). At the cap "
                "`set_trading_stop` refuses (ErrCode 110061) and a genuine "
                "protective tightening fails SILENTLY."
            )
        which, split_fields = _bybit_over_cover_condition(
            size=size, covered=covered, leg_side_split=leg_side_split,
        )
        report(
            "bybit_over_cover",
            "detected",
            level=Level.ERROR,
            reason=(
                f"{account_id}/{symbol}: position {size}. " + which
                + f" (side-blind SL total across all books: {covered}"
                + (f", {pct:.0f}% of the position" if pct is not None else "")
                + f", {leg_count} leg(s) — this is the figure that TRIPPED the "
                "check, not a claim about the graded book.)" + cap_note
                + " The legs are reduceOnly, so this is NOT a naked-reverse "
                "hazard (that is the IB/OCA shape) — the harm is the cap above "
                "and a leg owned by a CLOSED trade cutting a live position "
                "at a dead trade's level. Remedy: `cancel-stale-tpsl-legs`, "
                "DRY-RUN first, and CHECK ITS PLAN AGAINST THE JOURNAL. Its "
                "selector was ownership-based only from 2026-08-26; before that "
                "it kept the NEWEST leg by createdTime and would have cancelled "
                f"the live trade's leg ({_STALE_LEG_SELECTOR_BACKLOG_ID})."
            ),
            account_id=account_id,
            symbol=symbol,
            size=size,
            # ⚠️ UNCHANGED MEANING, deliberately: `covered_qty`/`over_cover_pct`
            # are still the SIDE-BLIND totals a pre-2026-09-02 consumer would
            # read. Re-pointing an existing key at a new population is how two
            # surfaces come to disagree while both look right; the per-book
            # figures ship as NEW keys below instead.
            covered_qty=covered,
            over_cover_pct=pct,
            # Structured beside the prose so a consumer can branch without
            # parsing the reason string.
            sl_leg_count=leg_count,
            protective_leg_count=combined,
            leg_cap=_BYBIT_LEG_CAP,
            # None, never a number, when the targets were not counted — a
            # fabricated headroom is the whole defect this replaced.
            leg_cap_headroom=headroom,
            **split_fields,
        )
    except Exception:  # noqa: BLE001 — an alert failure must never abort the sweep
        logger.exception(
            "_emit_bybit_over_cover_alert: alert failed for %s/%s",
            account_id, symbol,
        )
    return True


def _ib_broker_naked_check_interval_seconds() -> float:
    """Min seconds between IB broker-side naked sweeps.

    Reads ``IB_BROKER_NAKED_CHECK_SECONDS`` at call time (next-tick effect);
    falls back to ``_DEFAULT_IB_BROKER_NAKED_CHECK_SECONDS`` on missing /
    unparseable values, clamped ``>= 0``. ``0`` disables the sweep (it runs the
    account-wide IB order read, so it is a cadence-gated capability, not an
    always-on one).
    """
    raw = os.environ.get("IB_BROKER_NAKED_CHECK_SECONDS")
    if raw is None or str(raw).strip() == "":
        return float(_DEFAULT_IB_BROKER_NAKED_CHECK_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(_DEFAULT_IB_BROKER_NAKED_CHECK_SECONDS)

# Futures contract-month codes (CME/COMEX): one letter + 1-2 digit year,
# e.g. "MHGN6" (July 2026 micro copper) -> base "MHG". Used to normalize an
# adopted-orphan's specific-contract symbol back to the base symbol the rest
# of the system (signal builders, order packages, _build_contract) speaks.
_FUTURES_MONTH_SUFFIX = __import__("re").compile(r"^([A-Z]{2,})([FGHJKMNQUVXZ]\d{1,2})$")


def _base_futures_symbol(symbol: Optional[str]) -> str:
    """Strip a trailing futures contract-month code (``MHGN6`` -> ``MHG``).

    Returns the symbol unchanged when it carries no month suffix (spot/crypto
    like ``BTCUSDT``, or an already-base futures root like ``MES``)."""
    s = str(symbol or "").strip().upper()
    m = _FUTURES_MONTH_SUFFIX.match(s)
    return m.group(1) if m else s


def _resolve_protective_levels(db, symbol, direction):
    """Best-effort (sl, tp) for a naked position from the most recent
    matching order package. Matches on direction + the symbol or its base
    futures root (an adopted ``MHGN6`` resolves against the ``MHG`` package
    that spawned it). Returns ``(None, None)`` when nothing usable is found.
    """
    try:
        base = _base_futures_symbol(symbol)
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT sl, tp FROM order_packages "
                "WHERE direction=? AND symbol IN (?,?) "
                "AND sl IS NOT NULL AND sl>0 AND tp IS NOT NULL AND tp>0 "
                "ORDER BY created_at DESC LIMIT 1",
                (str(direction or "").lower(), str(symbol or ""), base),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return (None, None)
        return (row["sl"], row["tp"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("_resolve_protective_levels(%s): failed: %s", symbol, exc)
        return (None, None)


def _rearm_broker_protection_after_recovery(db, trade_id, sl, tp) -> bool:
    """Re-place a broker-side GTC SL/TP bracket on an orphan the reverse
    reconciler just adopted/re-attached.

    The reconciler recovers a position's SL/TP from its originating order
    package and writes them onto the journal row — but writing the *journal*
    fields does NOT put a protective order back at the broker. A re-adopted
    IBKR net position therefore stays NAKED at the exchange even though the
    dashboard now shows an SL/TP (BL-20260615-MGCNAKED — the MGC ``orphan_adopt``
    that sat with no stop). This re-arms the GTC OCA bracket via the same
    IB-only path as the naked-position sweep
    (:func:`_attempt_naked_autoprotect` -> ``IBClient.place_protective``).
    Unconditional baseline behaviour — re-arming protection on a recovered
    position is part of healing the orphan, not an opt-in feature.
    Best-effort; never raises.
    """
    if sl in (None, 0) or tp in (None, 0):
        return False
    try:
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            row = conn.execute(
                "SELECT id, account_id, symbol, direction, position_size "
                "FROM trades WHERE id=?",
                (int(trade_id),),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_rearm_broker_protection_after_recovery: read failed for "
            "trade_id=%s: %s", trade_id, exc,
        )
        return False
    if row is None:
        return False
    return _attempt_naked_autoprotect(row, sl, tp, db=db)


def _stamp_repair(db, row, kind: str, verified: str = "unverified") -> None:
    """Record on the TRADE ROW that its protective bracket was repaired.

    Thin adapter onto the single owner, ``Database.stamp_protection_repair`` —
    it exists only to resolve the trade id off a ``sqlite3.Row`` (no ``.get``)
    and to make a missing ``db`` a no-op rather than a crash.

    ⚠️ **``db=None`` skips the stamp and says so.** A caller that forgot to
    thread ``db`` loses the bookkeeping, never the repair: re-arming a naked
    live position outranks recording that we did (Prime Directive — the repair
    is the required capability). A silent skip would be the worse failure,
    since the column would read "never repaired" for a trade that was.

    ``verified`` defaults to ``unverified`` — *we did not look*. The naked
    sweep deliberately adds no broker read-back per repair (that is the IB
    pacing-wedge shape), so it can attest the call was accepted and nothing
    more. Only the re-assert path, which already pays for one read-back on an
    apply, passes a graded state.

    Best-effort; never raises into a live repair path.
    """
    if db is None:
        logger.debug(
            "_stamp_repair: no db handle for %s repair — the bracket WAS "
            "repaired, only the trade-row stamp is skipped", kind,
        )
        return
    try:
        trade_id = row["id"]
    except Exception:  # noqa: BLE001 -- a shapeless row must not break the repair
        return
    try:
        db.stamp_protection_repair(trade_id, kind, verified)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_stamp_repair: failed for trade_id=%s kind=%s: %s",
            trade_id, kind, exc,
        )


def _attempt_naked_autoprotect(row, sl, tp, *, db=None) -> bool:
    """Re-arm a broker-side GTC protective bracket on a naked position.

    Returns True on a placed protective bracket. Never raises.

    Per-broker (BL-20260629-ALPACA-NAKED-BRACKET extended this beyond IB):
      * **IB** — a GTC OCA bracket via ``IBClient.place_protective`` (futures
        root symbol). The classic naked-orphan re-arm.
      * **Alpaca** — a GTC OCO via ``AlpacaClient.place_protective``. The entry
        bracket's protective legs are ``time_in_force: day`` (Alpaca
        market-entry-bracket constraint) and are cancelled at the RTH close, so
        a multi-session equity hold goes broker-naked; this re-arms a GTC pair
        that survives the close. (Detection differs too — see
        ``_check_broker_naked_equity_positions``: the journal row keeps its
        sl/tp, so the DB-driven naked check never flags it.)
      * **Bybit** — a Full-mode position-level bracket via ``set_trading_stop``
        (BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT). Under
        ``BYBIT_TPSL_MODE=partial`` the per-trade qty-scoped legs desync from the
        netted one-way position (intent_reduce legs add none; a close cancels its
        own trade's legs; the 20-leg cap can block an amend), so a Bybit position
        CAN go naked. Full mode REPLACES (never adds) so the recovery re-arm can't
        itself trip the leg cap and covers the whole net position.
      * **OANDA** — no-op: SL/TP attach atomically at entry and the legs
        do not expire at a session boundary, so a naked state can't arise.

    Unconditional baseline behaviour — there is no enable flag. A live position
    with no stop is an unacceptable state the system must always correct, the
    same way an orphaned trade is a condition the reconciler always heals; it is
    not an opt-in feature to be toggled (Prime Directive: no default-off gate in
    front of a required capability).
    """
    account_id = str(row["account_id"] or "")
    symbol = str(row["symbol"] or "")
    direction = str(row["direction"] or "")
    try:
        qty = float(row["position_size"])
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    if qty <= 0 or sl in (None, 0) or tp in (None, 0):
        return False
    try:
        from src.bot import data_loaders
        from src.units.accounts.clients import alpaca_client_for, ib_client_for

        accounts = data_loaders.list_accounts() or []
        acc = next(
            (a for a in accounts if a.get("account_id") == account_id), None
        )
        if acc is None:
            return False
        exchange = str(acc.get("exchange", "")).lower()
        if exchange == "bybit":
            # BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT: Bybit CAN go naked —
            # under BYBIT_TPSL_MODE=partial the qty-scoped SL/TP legs desync from
            # the NETTED one-way position (intent_reduce legs add none; a close
            # cancels its own trade's legs; the 20-leg cap can block an amend),
            # so the earlier "atomic at entry, can't go naked" assumption is
            # false. Re-arm a POSITION-LEVEL (Full-mode) bracket over the whole
            # net position via set_trading_stop — Full REPLACES rather than adds,
            # so a recovery re-arm never grows the leg count (never itself trips
            # the cap) and guarantees the entire naked position is protected.
            from src.units.accounts.clients import bybit_client_for
            from src.units.accounts.execute import _bybit_category
            client = bybit_client_for(acc)
            if client is None:
                return False
            category = _bybit_category(acc)
            if category == "spot":
                return False  # spot has no position-level SL/TP; monitor exits it
            # positionIdx was HARDCODED 0 here (one-way). Kept as the default
            # via the resolver so the re-arm cannot become the one Bybit write
            # that ignores hedge mode once the allowlist is non-empty — a
            # half-applied position-mode change would re-arm a protective stop
            # against the WRONG book, which is worse than not re-arming.
            # Inert today: an empty allowlist resolves one_way and the kwarg
            # stays 0, byte-for-byte unchanged.
            from src.runtime.bybit_position_mode import position_idx_for
            # `row` may be a sqlite3.Row (no .get), and `direction` is not
            # guaranteed to be selected — resolve defensively rather than
            # assuming a shape. An unreadable direction lands as `unresolved`,
            # which keeps the 0 default and logs, instead of raising into the
            # re-arm path that exists to fix a naked position.
            try:
                _dir = row["direction"]
            except Exception:  # noqa: BLE001 — a missing column is not a failure
                _dir = None
            _pos = position_idx_for(account_id, symbol, _dir)
            resp = client.set_trading_stop(
                category=category,
                symbol=symbol,
                positionIdx=(0 if _pos.idx is None else _pos.idx),
                tpslMode="Full",
                stopLoss=str(sl),
                takeProfit=str(tp),
            )
            ret_code = (resp or {}).get("retCode")
            if ret_code in (0, "0", None):
                _stamp_repair(db, row, "naked_rearm")
                return True
            logger.warning(
                "_attempt_naked_autoprotect: bybit set_trading_stop refused for "
                "trade_id=%s: retCode=%s retMsg=%s",
                row["id"], ret_code, (resp or {}).get("retMsg"),
            )
            return False
        if exchange in ("interactive_brokers", "ib"):
            client = ib_client_for(acc, readonly=False)
            protect_symbol = _base_futures_symbol(symbol)
        elif exchange == "alpaca":
            client = alpaca_client_for(acc)
            protect_symbol = symbol
        else:
            return False  # not a re-armable broker (oanda atomic at entry)
        if client is None:
            return False
        resp = client.place_protective(
            {
                "symbol": protect_symbol,
                "direction": direction,
                "qty": qty,
                "sl": sl,
                "tp": tp,
                # Scope the re-arm's pre-cancel to THIS trade's own OCA group.
                # Without it place_protective cancels every resting leg on the
                # symbol root, which on a netted IB contract destroys the OTHER
                # strategies' take-profit legs
                # (BL-20260814-IB-PROTECTION-BOOLEAN-NOT-QUANTITY).
                "oca_key": str(row["id"]),
                # Scopes the stray-group sweep's CANCEL to the allowlist
                # (PROTECTION_STRAY_GROUP_ACCOUNTS). Absent -> never cancels.
                # ⚠️ `account_id`, the local, NOT `row.get(...)` — `row` is a
                # sqlite3.Row, which has no `.get`. Using it raised into this
                # function's broad `except` and silently failed EVERY naked
                # re-arm; caught by tests/test_ib_naked_rearm.py.
                "account_id": account_id,
            }
        )
        if not resp or resp.get("retCode") != 0:
            logger.warning(
                "_attempt_naked_autoprotect: place_protective refused for "
                "trade_id=%s: %r", row["id"], (resp or {}).get("retMsg"),
            )
            return False
        _stamp_repair(db, row, "naked_rearm")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_attempt_naked_autoprotect: failed for trade_id=%s: %s",
            row["id"], exc,
        )
        return False


def _check_broker_naked_equity_positions(db) -> Dict[str, int]:
    """Re-arm GTC protection on Alpaca positions that are NAKED at the broker.

    The DB-driven :func:`_check_naked_positions` only flags rows whose *journal*
    sl/tp is missing — but an Alpaca position keeps its journal sl/tp while its
    broker-side day-TIF bracket legs are cancelled at the RTH close, so it is
    broker-naked yet invisible to that check (BL-20260629-ALPACA-NAKED-BRACKET).
    This pass closes that gap: for each open, past-grace Alpaca position it asks
    the broker which SIDES of the bracket actually rest
    (``AlpacaClient.protection_state`` — **not** the boolean
    ``has_protective_orders``, which answers ``True`` for a stop-only book and
    is the grading BL-20260816-COVERAGE-IS-ONE-SIDED exists to stop) and, when
    the stop side is absent, re-arms a GTC OCO via
    :func:`_attempt_naked_autoprotect` (levels from the journal row, or the
    originating order package as a fallback). The TARGET side is counted and
    alerted without a blind re-arm, mirroring the IB sweep: a missing stop is a
    safety gap to close, a missing take-profit is decision-time geometry.

    The broker's own order state IS the idempotency: a position that already has
    a resting leg is skipped, so this never stacks OCOs and self-corrects each
    tick. A read failure (``protection_state`` → ``None``) is skipped — a
    transient outage must not be read as naked. Never raises.

    Returns ``{"checked", "broker_naked", "rearmed", "errors"}``.
    """
    summary: Dict[str, int] = {
        "checked": 0, "broker_naked": 0, "rearmed": 0, "errors": 0,
        # Graded SEPARATELY from the stop side, mirroring the IB sweep: a
        # position can be fully stop-covered and hold no take-profit at all
        # (BL-20260816-COVERAGE-IS-ONE-SIDED). Alerted, never re-armed.
        "target_naked": 0,
    }
    try:
        from src.bot import data_loaders
        from src.units.accounts.clients import alpaca_client_for

        accounts = data_loaders.list_accounts() or []
        alpaca_ids = {
            str(a.get("account_id"))
            for a in accounts
            if str(a.get("exchange", "")).lower() == "alpaca"
        }
        if not alpaca_ids:
            return summary
        acc_by_id = {str(a.get("account_id")): a for a in accounts}

        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, position_size, "
                "stop_loss, take_profit_1, created_at, notes "
                "FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_check_broker_naked_equity_positions: read failed: %s", exc)
        summary["errors"] += 1
        return summary

    now = datetime.now(timezone.utc)
    clients: Dict[str, object] = {}
    for row in rows:
        account_id = str(row["account_id"] or "")
        if account_id not in alpaca_ids:
            continue
        summary["checked"] += 1
        created = _parse_created_at(row["created_at"])
        if (
            created is not None
            and (now - created).total_seconds() < _NAKED_POSITION_GRACE_SECONDS
        ):
            continue  # fresh fill may not have propagated; let the entry settle
        symbol = str(row["symbol"] or "")
        if not symbol:
            continue
        # Skip a position the monitor is ACTIVELY CLOSING this tick
        # (BL-20260708-ALPACA-REARM-VS-CLOSE-FIGHT). Re-arming a protective OCO
        # on it would re-hold the shares the close is trying to sell, and the two
        # fight forever (the QQQ #3269 perpetual close-failure). Let the close win.
        if is_active_close(account_id, symbol):
            logger.info(
                "_check_broker_naked_equity_positions: skipping re-arm for "
                "%s/%s — an active close is in flight this tick (let it flatten)",
                account_id, symbol,
            )
            continue
        try:
            if account_id not in clients:
                clients[account_id] = alpaca_client_for(acc_by_id[account_id])
            client = clients[account_id]
            if client is None:
                continue
            # Sides graded SEPARATELY. `has_protective_orders` returns True for
            # a stop-only book, so consuming it here read "protected" over a
            # position that can only stop out or run — the Alpaca half of
            # BL-20260816-COVERAGE-IS-ONE-SIDED, over 13 live positions.
            state = client.protection_state(symbol)
            if state is None:
                continue  # read failure — never act on an unconfirmed read
            if state.get("target") is False and row["take_profit_1"] not in (None, 0):
                # TARGET-naked while the journal DECLARES a take-profit. Alert,
                # do NOT re-arm: a missing stop is a safety gap worth closing
                # blind, but a target is decision-time geometry a repair must
                # read rather than invent, and placing one is an order the
                # strategy did not ask for at this instant. Same split the IB
                # sweep makes; the repair wire is the `attach-ib-target`
                # action's Alpaca analogue, not this sweep.
                summary["target_naked"] += 1
                _emit_target_naked_alert(
                    account_id=account_id,
                    symbol=symbol,
                    size=_safe_float(row["position_size"]) or 0.0,
                    target_qty=0.0,          # zero TP legs rest on this symbol
                    stop_qty=None,           # Alpaca grades sides, not qty
                    declared_tp=row["take_profit_1"],
                    trade_id=row["id"],
                    venue="alpaca",
                )
            if state.get("stop"):
                continue  # stop side is armed — nothing for THIS pass to do
            summary["broker_naked"] += 1
            sl = row["stop_loss"]
            tp = row["take_profit_1"]
            a_sl = sl if (sl not in (None, 0) and sl > 0) else None
            a_tp = tp if (tp not in (None, 0) and tp > 0) else None
            if a_sl is None or a_tp is None:
                r_sl, r_tp = _resolve_protective_levels(
                    db, symbol, str(row["direction"] or "")
                )
                a_sl = a_sl if a_sl is not None else r_sl
                a_tp = a_tp if a_tp is not None else r_tp
            if a_sl is None or a_tp is None:
                logger.warning(
                    "_check_broker_naked_equity_positions: trade_id=%s %s "
                    "broker-naked but no SL/TP resolvable — leaving for alert",
                    row["id"], symbol,
                )
                continue
            if _attempt_naked_autoprotect(row, a_sl, a_tp, db=db):
                summary["rearmed"] += 1
                logger.info(
                    "_check_broker_naked_equity_positions: re-armed GTC OCO "
                    "(sl=%s tp=%s) on broker-naked trade_id=%s account=%s "
                    "symbol=%s", a_sl, a_tp, row["id"], account_id, symbol,
                )
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            logger.warning(
                "_check_broker_naked_equity_positions: failed for trade_id=%s: %s",
                row["id"], exc,
            )
    return summary


#: In-process re-assert state per ``(account_id, protect_symbol)``:
#: ``{"last": <monotonic seconds>, "attempts": int}``. Per-PROCESS on purpose —
#: a restart re-arms the budget, which is fail-SAFE here: the worst case is one
#: extra correctly-levelled re-assert after a deploy, whereas persisting a spent
#: budget across restarts would leave a genuinely diverged position unrepaired
#: with nothing saying why.
_REASSERT_STATE: Dict[Tuple[str, str], Dict[str, float]] = {}


def _reassert_soak_path():
    from src.utils.paths import runtime_logs_dir
    return os.path.join(runtime_logs_dir(), "protection_reassert_soak.jsonl")


def _reassert_from_divergence(
    db, *, client, account_id: str, protect_symbol: str, row,
    price_verdict: Dict[str, Any], summary: Dict[str, int],
) -> None:
    """Decide, record, and (only at ``apply`` on an allowlisted account) act.

    Best-effort throughout: this runs inside the naked sweep on the live
    trader, so it must never raise into it. Every path writes a soak row, so
    ``annotate`` produces the exact list to review before the ``apply`` flip —
    the ``NETTING_ATTRIBUTION_MODE`` staging shape.
    """
    import json as _json
    import time as _time
    try:
        from src.runtime.protection_reassert import (
            MODE_APPLY, STATE_REASSERT, account_may_apply, decide_reassert,
            resolve_mode,
        )
        mode = resolve_mode(os.environ.get("PROTECTION_REASSERT_MODE"))
        key = (account_id, protect_symbol)
        st = _REASSERT_STATE.get(key) or {}
        last = st.get("last")
        since = (_time.monotonic() - last) if last is not None else None

        def _num(name, default):
            try:
                return float(os.environ.get(name) or default)
            except (TypeError, ValueError):
                return float(default)  # a typo must not change behaviour

        decision = decide_reassert(
            price_verdict=price_verdict,
            declared_sl=row["stop_loss"],
            declared_tp=row["take_profit_1"],
            position_size=row["position_size"],
            trade_is_open=True,  # the query selects status='open' only
            seconds_since_last_attempt=since,
            attempts_so_far=int(st.get("attempts") or 0),
            cooldown_s=_num("PROTECTION_REASSERT_COOLDOWN_S", 3600.0),
            max_attempts=int(_num("PROTECTION_REASSERT_MAX_ATTEMPTS", 3)),
        )

        may_apply = account_may_apply(
            account_id, os.environ.get("PROTECTION_REASSERT_ACCOUNTS"))
        will_act = (
            mode == MODE_APPLY
            and may_apply
            and decision["state"] == STATE_REASSERT
        )
        # `apply_scope` says WHY a decision to act did not act, so a held-back
        # row can never read as an applied one (the netting-soak lesson).
        scope = ("allowlisted" if may_apply else "not_allowlisted")

        rec: Dict[str, Any] = {
            "at": datetime.now(timezone.utc).isoformat(),
            "account_id": account_id, "symbol": protect_symbol,
            "trade_id": row["id"], "direction": row["direction"],
            "position_size": row["position_size"],
            "declared_sl": row["stop_loss"], "declared_tp": row["take_profit_1"],
            "resting_stop": price_verdict.get("nearest_resting"),
            "ticks": price_verdict.get("ticks"),
            "exposure": price_verdict.get("exposure"),
            "decision": decision["state"], "reason": decision["reason"],
            "mode": mode, "apply_scope": scope, "acted": will_act,
            "attempts_so_far": int(st.get("attempts") or 0),
        }

        if will_act:
            levels = decision["levels"]
            _REASSERT_STATE[key] = {
                "last": _time.monotonic(),
                "attempts": int(st.get("attempts") or 0) + 1,
            }
            result = client.modify_protective({
                "symbol": protect_symbol,
                "direction": row["direction"],
                "qty": abs(float(row["position_size"] or 0.0)),
                "sl": levels["sl"], "tp": levels["tp"],
                # Same reason as the trailing amend: without this the re-arm's
                # pre-cancel is SYMBOL-WIDE and can strip a sibling trade's
                # protective legs on a netted IB contract. This path is inert at
                # the default PROTECTION_REASSERT_MODE=annotate, which is
                # exactly why it had to be fixed NOW -- it would have inherited
                # the defect silently on the flip to `apply`.
                "oca_key": str(row["id"]),
                # Same as the naked re-arm above: scopes the stray-group
                # sweep's CANCEL. Absent -> never cancels.
                # ⚠️ Bracket access — `row` is a sqlite3.Row with no `.get`.
                "account_id": row["account_id"],
            })
            call_ok = int((result or {}).get("retCode", 1)) == 0
            rec["result"] = result
            # ⚠️ `retCode == 0` PROVES THE CALL SUCCEEDED, NOT THAT BOTH LEGS REST.
            # `place_protective` places a stop AND a limit but returns a SINGLE
            # orderId, so an envelope-only check cannot see a half-armed bracket.
            # Measured 2026-08-23 one hour after arming: MES 4350 re-asserted with
            # declared_tp 8390.59025, returned retCode 0, and the account-wide
            # broker read showed ONE leg -- STP only, no limit order anywhere.
            # This is BL-20260816-COVERAGE-IS-ONE-SIDED at the opposite end of the
            # loop: that row is the DETECTION path grading a stop-only book as
            # covered; this was the REPAIR path reporting a stop-only re-arm as
            # complete. `BL-20260823-REASSERT-REPORTS-APPLIED-OK-ON-A-HALF-ARMED-BRACKET`.
            #
            # ONE extra broker read, and only on an APPLY -- which is rare by
            # construction (divergence + allowlist + cooldown + attempt cap). This
            # is NOT the per-pass read the design forbids.
            rec["applied_state"] = _reassert_applied_state(
                client, protect_symbol, call_ok,
                want_tp=levels.get("tp") is not None)
            ok = rec["applied_state"] == "both_legs_resting"
            rec["applied_ok"] = ok
            # Stamp the TRADE ROW, not just the soak log. The soak answers
            # "what did the re-assert do?"; the row answers "was THIS trade
            # repaired?" — which is the question that has been unanswerable,
            # and the reason the historical population is already lost.
            # `applied_state` is passed through verbatim: this path pays for a
            # read-back, so unlike the naked sweep it can say what actually
            # rests rather than only that the call was accepted.
            _stamp_repair(db, row, "reassert", rec["applied_state"])
            # The SUMMARY must not collapse either: "we could not verify" is not
            # "it failed", and a stop-only re-arm is neither. Counting all three
            # as one number reproduces, in the roll-up, the exact defect the
            # applied_state split exists to remove.
            _counter = {
                "both_legs_resting": "stop_price_reasserted",
                "stop_only": "stop_price_reassert_incomplete",
                "no_legs_resting": "stop_price_reassert_incomplete",
                "unverified": "stop_price_reassert_unverified",
                "call_failed": "stop_price_reassert_failed",
            }[rec["applied_state"]]
            # `.get(...) + 1`, not `summary[k] += 1`: this whole function sits
            # inside a broad `except` that logs and moves on, so a KeyError from a
            # caller whose summary predates a counter would SILENTLY kill the
            # re-assert — the decision, the log line and the soak row all vanish,
            # and the sweep reports nothing wrong. Found exactly that way: adding
            # two counters turned a passing wiring test into "re-assert decision
            # failed" with no other signal.
            summary[_counter] = int(summary.get(_counter, 0)) + 1
            logger.log(
                logging.INFO if ok else (
                    logging.ERROR if rec["applied_state"] == "call_failed"
                    else logging.WARNING),
                "_check_broker_naked_ib_positions: RE-ASSERT %s %s/%s trade=%s "
                "-> sl=%s tp=%s (was resting %s, %.1f ticks %s): %s",
                rec["applied_state"], account_id, protect_symbol, row["id"],
                levels["sl"], levels["tp"], price_verdict.get("nearest_resting"),
                price_verdict.get("ticks") or 0.0,
                price_verdict.get("exposure") or "consequence unknown", result,
            )
        else:
            summary["stop_price_reassert_annotated"] += 1

        try:
            path = _reassert_soak_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(rec, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001 -- the soak must never break the sweep
            logger.debug("protection re-assert soak write failed: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_check_broker_naked_ib_positions: re-assert decision failed for "
            "%s/%s: %s", account_id, protect_symbol, exc,
        )


def _reassert_applied_state(client, symbol, call_ok, *, want_tp):
    """Did the re-assert actually leave both declared legs resting at the venue?

    Five states, never collapsed — the same discipline the decision half uses:

    ``call_failed``        the modify itself returned non-zero; nothing to verify.
    ``both_legs_resting``  a stop AND a target rest. The only success.
    ``stop_only``          the stop rests, the target does not. NOT a success: the
                           position can stop out or run, and cannot take profit.
    ``no_legs_resting``    the call said OK and nothing rests. Worse than a
                           failure, because the call claimed otherwise.
    ``unverified``         the coverage read failed — *we could not look*. NEVER
                           folded into success; an unconfirmed read is exactly
                           what the sweep refuses to re-arm on, so it is also not
                           something to declare repaired on.
    """
    if not call_ok:
        return "call_failed"
    try:
        cov = client.protection_coverage(symbol)
    except Exception:  # noqa: BLE001 — a verification failure must not raise
        return "unverified"
    if not isinstance(cov, dict):
        return "unverified"          # `None` is the documented could-not-read
    stop = cov.get("stop_qty")
    target = cov.get("target_qty")
    if stop is None or (want_tp and target is None):
        return "unverified"          # the side we need was not graded
    has_stop = float(stop or 0.0) > 0.0
    has_target = float(target or 0.0) > 0.0
    if has_stop and (has_target or not want_tp):
        return "both_legs_resting"
    if has_stop:
        return "stop_only"
    return "no_legs_resting"


def _check_broker_naked_ib_positions(db) -> Dict[str, int]:
    """Re-arm GTC OCA protection on IB positions that are NAKED at the broker.

    The IB analogue of :func:`_check_broker_naked_equity_positions`
    (BL-20260709-IB-BROKER-PROTECTION-UNVERIFIED). The DB-driven
    :func:`_check_naked_positions` only flags a row whose *journal* SL/TP is
    missing — but an IB futures/ETF position whose broker OCA bracket was never
    placed, got cancelled, or was dropped during a Gateway breaker-flap keeps
    its journal SL/TP and so is invisible to that check while sitting
    broker-naked (the 2026-07-09 MGC monitor-blind incident: the alert claimed
    "Broker SL/TP backstop still holds" with no way to VERIFY it). This pass
    closes that gap: for each open, past-grace IB position it asks the broker
    **how much protection actually rests** (``IBClient.protection_coverage``,
    an account-wide ``reqAllOpenOrders`` read) and, when none does, re-arms a
    GTC OCA via :func:`_attempt_naked_autoprotect` (the SAME re-arm path the
    reconciler and DB-naked check use).

    .. note:: This docstring named ``IBClient.has_protective_orders`` until
       2026-08-16. That is a DIFFERENT accessor — a boolean "does any leg
       rest?" — and the body has never called it here. The distinction is the
       whole of ``BL-20260816-COVERAGE-IS-ONE-SIDED``: the boolean answers
       ``True`` for a stop-only book, so naming it invited exactly the reading
       that let two live positions sit target-naked. Coverage is a QUANTITY,
       and since 2026-08-16 a TWO-SIDED one — ``stop_qty`` and ``target_qty``
       are returned separately, because a stop and a take-profit are not
       interchangeable.

    **Cadence-gated** (build constraint 1): the account-wide IB order read is
    NOT run every monitor tick — it runs at most once per
    ``_ib_broker_naked_check_interval_seconds`` to stay clear of the IB
    tick-latency / pacing wedge class (BL-20260609). A read failure
    (``protection_coverage`` → ``None`` — breaker open, gateway wedged,
    ambiguous) is skipped: a transient outage must never be read as naked
    (build constraint 3, fail-safe: never PLACE on an unconfirmed read). Never
    raises.

    Returns ``{"checked", "broker_naked", "rearmed", "errors", "skipped",
    "partially_naked", "ungradeable", "read_failed", "covered",
    "target_naked"}``. The last five were previously undocumented; a caller
    reading the old five-key contract would have missed ``target_naked``
    entirely, which is the one this sweep exists to surface.
    """
    global _LAST_IB_BROKER_NAKED_CHECK_MONO
    summary: Dict[str, int] = {
        "checked": 0, "broker_naked": 0, "rearmed": 0, "errors": 0, "skipped": 0,
        "partially_naked": 0, "ungradeable": 0, "read_failed": 0, "covered": 0,
        # Declared here, not created on first use: a sweep that found no
        # over-cover must report 0 rather than omit the key, or "we looked and
        # found none" reads as "we did not look" — which is what this detector
        # exists to stop being true.
        "over_covered": 0,
        # Graded SEPARATELY from the stop side: a position can be fully
        # stop-covered and hold no target at all (BL-20260816-COVERAGE-IS-ONE-SIDED).
        "target_naked": 0,
        # Graded on PRICE, not quantity: a stop can cover the full size on
        # the correct side and still rest nowhere near the declared level
        # (BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND).
        "stop_price_diverges": 0,
        # Re-assert outcomes, all four declared up front for the same reason
        # as the keys above: a sweep that re-asserted nothing must report 0,
        # not omit the key. `annotated` counts the divergences a decision
        # DECLINED to act on (mode, allowlist, cooldown, budget) — without it a
        # quiet run cannot be told from a run where the trigger never fired.
        "stop_price_reasserted": 0,          # verified: BOTH legs rest
        "stop_price_reassert_incomplete": 0,  # call OK, bracket half-armed
        "stop_price_reassert_unverified": 0,  # could not re-read the venue
        "stop_price_reassert_failed": 0,      # the modify itself was rejected
        "stop_price_reassert_annotated": 0,
    }
    interval = _ib_broker_naked_check_interval_seconds()
    if interval <= 0:
        summary["skipped"] = 1
        return summary  # sweep disabled (IB_BROKER_NAKED_CHECK_SECONDS<=0)
    now_mono = time.monotonic()
    if (now_mono - _LAST_IB_BROKER_NAKED_CHECK_MONO) < interval:
        summary["skipped"] = 1
        return summary  # within the cadence window — not this tick
    _LAST_IB_BROKER_NAKED_CHECK_MONO = now_mono

    try:
        from src.bot import data_loaders
        from src.units.accounts.clients import ib_client_for

        accounts = data_loaders.list_accounts() or []
        ib_ids = {
            str(a.get("account_id"))
            for a in accounts
            if str(a.get("exchange", "")).lower() in ("interactive_brokers", "ib")
        }
        if not ib_ids:
            return summary
        acc_by_id = {str(a.get("account_id")): a for a in accounts}

        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, position_size, "
                "stop_loss, take_profit_1, created_at, notes "
                "FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_check_broker_naked_ib_positions: read failed: %s", exc)
        summary["errors"] += 1
        return summary

    now = datetime.now(timezone.utc)
    clients: Dict[str, object] = {}
    # Cache the account-wide has_protective_orders verdict per
    # (account_id, protect_symbol) within this sweep so two open trades on the
    # same symbol don't each pay a fresh reqAllOpenOrders read.
    protected_cache: Dict[Tuple[str, str], Optional[bool]] = {}
    for row in rows:
        account_id = str(row["account_id"] or "")
        if account_id not in ib_ids:
            continue
        summary["checked"] += 1
        created = _parse_created_at(row["created_at"])
        if (
            created is not None
            and (now - created).total_seconds() < _NAKED_POSITION_GRACE_SECONDS
        ):
            continue  # fresh fill may not have propagated; let the entry settle
        symbol = str(row["symbol"] or "")
        if not symbol:
            continue
        # Match the has_protective read on the generic root (contract.symbol
        # axis) — an adopted ``MHGN6`` resolves to the ``MHG`` bracket.
        protect_symbol = _base_futures_symbol(symbol)
        # Skip a position the monitor is ACTIVELY CLOSING this tick — re-arming
        # a protective OCA on it would fight the in-flight close (mirrors the
        # equity sweep's BL-20260708-ALPACA-REARM-VS-CLOSE-FIGHT guard).
        if is_active_close(account_id, symbol):
            logger.info(
                "_check_broker_naked_ib_positions: skipping re-arm for "
                "%s/%s — an active close is in flight this tick (let it flatten)",
                account_id, symbol,
            )
            continue
        try:
            if account_id not in clients:
                clients[account_id] = ib_client_for(
                    acc_by_id[account_id], readonly=False
                )
            client = clients[account_id]
            if client is None:
                continue
            cache_key = (account_id, protect_symbol)
            if cache_key not in protected_cache:
                protected_cache[cache_key] = client.protection_coverage(
                    protect_symbol
                )
            cov = protected_cache[cache_key]
            if cov is None:
                # Fail-safe skip (never re-arm on an unconfirmed read) — but say
                # so. A bare `continue` here made "the sweep ran and could not
                # read" indistinguishable from "the sweep never ran", which is
                # the collapsed-state shape this repo has a guard for, and it
                # blocked verifying the coverage fix on the 2026-08-14 deploy:
                # the post-restart breaker was open, every read returned None,
                # and the logs were silent either way.
                summary["read_failed"] += 1
                logger.info(
                    "_check_broker_naked_ib_positions: %s/%s coverage read "
                    "unavailable (breaker open / gateway unreachable) — "
                    "skipping, protection left as-is",
                    account_id, protect_symbol,
                )
                continue
            size = float(cov.get("size") or 0.0)
            covered = float(cov.get("covered_qty") or 0.0)
            if size <= 0:
                continue  # flat at the broker — nothing to protect
            if cov.get("unknown_qty_legs"):
                # Coverage is UNGRADEABLE, not zero. Re-arming here would stamp
                # this trade's geometry over the whole netted position; skipping
                # leaves whatever protection exists intact. Mirrors the Bybit
                # sweep's refusal to guess.
                summary["ungradeable"] += 1
                logger.warning(
                    "_check_broker_naked_ib_positions: %s/%s has %d protective "
                    "leg(s) with unparseable qty — coverage ungradeable, "
                    "skipping re-arm rather than guessing",
                    account_id, protect_symbol, cov["unknown_qty_legs"],
                )
                continue

            # ── UPPER bound. This sweep only ever asked whether coverage was
            # SUFFICIENT (BL-20260816-IB-STOPS-OVER-COVER-IN-DISJOINT-OCA-GROUPS).
            # Measured 2026-08-16: ib_paper MES held stops 338 (15 @ 7516.50,
            # oca-protect-336) and 375 (15 @ 7533.75, oca-protect-373) — 30
            # contracts against a 15 long — and passed, because 30 >= 15.
            #
            # ⚠️ WHAT MAKES IT DANGEROUS IS THE GROUP COUNT, NOT THE RATIO, and a
            # blind port of the Bybit factor would miss that. ocaType=1 cancels the
            # rest of the SAME group when a leg fills; it says nothing about a
            # DIFFERENT group. So excess inside ONE group is self-limiting, while
            # excess spread across TWO is the sequence: stop A fires -> flat ->
            # stop B is still resting -> it fires -> the account is SHORT, with no
            # stop of its own. Graded at two levels rather than one alarm, because
            # a detector that shouts equally at both is the desensitised-alarm P1.
            #
            # DETECT-ONLY, deliberately. It must not cancel a leg: choosing WHICH
            # leg to cancel is exactly what went wrong on 2026-08-20, when the
            # remediation cancelled the one matching the journal and left the stray
            # (BL-20260820-OVERCOVER-REMEDIATION-CANCELLED-THE-JOURNAL-MATCHING-LEG:
            # MES still rests at 7516.5 against a declared 7533.69642857 —
            # 17.196429 pts x 15 x 5 = $1,289.73).
            # PRICE. Everything above grades QUANTITY and SIDE; none of it can
            # answer "is protection resting WHERE WE DECLARED?"
            # (BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND). A stop covering
            # the full size on the correct side grades FULLY COVERED however far
            # from the journal's level it sits -- measured live 2026-08-23 on
            # ib_paper MES 4350: declared 7533.696429, resting 7516.5, 68.8
            # ticks, and at $5/pt x 15 contracts that is $1,289.72 of protection
            # the position does not have. This sweep had never flagged it; an
            # offline reconciler run is what found it.
            #
            # DETECT-ONLY, for the same reason the over-cover check below is:
            # choosing what to do about a divergence -- amend, or cancel and
            # re-arm -- is exactly what went wrong on 2026-08-20, when the
            # remediation cancelled the leg that MATCHED the journal and kept
            # the stray. Grading is Tier-1; touching a live protective order is
            # not, and this sweep does not do it.
            #
            # The verdict comes from src.runtime.protection_price, the same pure
            # module scripts/ops/broker_bracket_reconcile.py grades with, so the
            # live sweep and the offline report can never disagree about a level.
            try:
                from src.core.profile_loader import (
                    contract_value_usd_for, tick_size_for,
                )
                from src.runtime.protection_price import (
                    PRICE_DIVERGES, grade_protection_price,
                )
                _pv = grade_protection_price(
                    declared=row["stop_loss"],
                    resting_prices=cov.get("stop_prices"),
                    direction=row["direction"],
                    side="stop",
                    tick_size=tick_size_for(protect_symbol),
                )
                if _pv["state"] == PRICE_DIVERGES:
                    summary["stop_price_diverges"] += 1
                    # RE-ASSERT (2026-08-23, operator-directed): "we need to be
                    # able to adjust trades on IB without disconnecting the
                    # integration ... we can't leave it to chance because of one
                    # trade not being worth the effort."
                    #
                    # The mechanism already existed — `modify_protective` runs
                    # on the TRADER'S OWN client, so it needs no ops clientId and
                    # evicts nothing. Only the TRIGGER was missing, and its
                    # absence was structural: `interpret_verdict` compares intent
                    # to `order_packages.sl`, never to the venue, so a diverged
                    # leg can never be re-synced by the normal path
                    # (BL-20260823-MODIFY-IDEMPOTENCE-COMPARES-INTENT-TO-JOURNAL-NEVER-TO-VENUE).
                    #
                    # The DECISION is a pure function so the policy is arguable
                    # in tests rather than against a live position — which is
                    # exactly what went wrong on 2026-08-20.
                    _reassert_from_divergence(
                        db, client=client, account_id=account_id,
                        protect_symbol=protect_symbol, row=row,
                        price_verdict=_pv, summary=summary,
                    )
                    _gap = abs(_pv["diff"]) * contract_value_usd_for(
                        protect_symbol) * size
                    logger.error(
                        "_check_broker_naked_ib_positions: STOP PRICE DIVERGES "
                        "%s/%s trade=%s -- journal declares %s but the nearest "
                        "resting stop is %s (%.1f ticks %s the declared level; "
                        "%s). ~$%.2f of declared protection is not actually "
                        "resting. Detect-only: resolve by hand, and READ "
                        "trades.stop_loss rather than taking a level from a "
                        "caller.",
                        account_id, protect_symbol, row["id"],
                        _pv["declared"], _pv["nearest_resting"], _pv["ticks"],
                        _pv["side_of_declared"],
                        # `exposure` is None when the direction was unreadable.
                        # Printing a bare "None" beside a confident geometry
                        # invites the reader to supply the missing half; say
                        # which fact is absent instead.
                        _pv["exposure"] or "consequence UNKNOWN (direction "
                        "unreadable — the geometry above stands, its meaning "
                        "does not)",
                        _gap,
                    )
            except Exception as exc:  # noqa: BLE001 -- grading never breaks the sweep
                logger.debug(
                    "_check_broker_naked_ib_positions: price grading failed "
                    "for %s/%s: %s", account_id, protect_symbol, exc,
                )

            _stop_q = float(cov.get("stop_qty") or 0.0)
            if _stop_q > size * _IB_OVERCOVER_FACTOR:
                summary["over_covered"] += 1
                _groups = cov.get("oca_groups") or {}
                _pct = (100.0 * _stop_q / size) if size else 0.0
                if len(_groups) > 1:
                    logger.error(
                        "_check_broker_naked_ib_positions: STOP OVER-COVER ACROSS "
                        "DISJOINT OCA GROUPS %s/%s — position size=%s but resting "
                        "STOP qty totals %s (%.0f%%) across %d groups %s. OCA "
                        "cancels only WITHIN a group, so one stop firing flattens "
                        "the position and leaves the other(s) resting to sell "
                        "again into a naked SHORT. Detect-only: resolve by hand, "
                        "and cancel the leg that does NOT match trades.stop_loss.",
                        account_id, protect_symbol, size, _stop_q, _pct,
                        len(_groups), sorted(_groups),
                    )
                    # The logger.error above reaches the journal and NOTHING
                    # a human reads. This is the operator surface.
                    _emit_stop_over_cover_alert(
                        account_id=account_id,
                        symbol=protect_symbol,
                        size=size,
                        stop_qty=_stop_q,
                        oca_groups=_groups,
                        # `.get` rather than `[...]`: a coverage dict from a
                        # client predating these keys must degrade to an
                        # "unknown" owner, never raise inside a safety page.
                        group_client_ids=cov.get("oca_group_client_ids"),
                        reader_client_id=cov.get("reader_client_id"),
                    )
                else:
                    logger.warning(
                        "_check_broker_naked_ib_positions: stop over-cover WITHIN "
                        "one OCA group %s/%s — position size=%s but resting STOP "
                        "qty totals %s (%.0f%%) in group(s) %s. Self-limiting (a "
                        "fill cancels its own group's siblings) but still not the "
                        "shape place_protective creates — worth a look.",
                        account_id, protect_symbol, size, _stop_q, _pct,
                        sorted(_groups),
                    )
            # TARGET-side coverage, graded separately (2026-08-16,
            # BL-20260816-COVERAGE-IS-ONE-SIDED). `covered_qty` counts a stop
            # and a take-profit as interchangeable, so a position holding a
            # full stop and NO target grades FULLY COVERED and this sweep has
            # never once fired on a stripped target. Measured on ib_paper the
            # same day: MGC 105 long with one stop and no limit, MES 15 long
            # with TWO stops and no limit — zero limit orders account-wide.
            #
            # This ALERTS and does not re-arm, deliberately. `place_protective`
            # is reached with oca_key=trade_id, so it cancels group
            # `oca-protect-t<id>` — while the legs actually resting sit in
            # `oca-protect-<reqId>` groups it will not match. A re-arm would
            # therefore ADD a stop+TP pair on top of the surviving stop (MGC:
            # 210 contracts of stop against 105) and, once the new target
            # filled and cancelled its own OCA sibling, leave the stray stop
            # resting on a FLAT book — able to fill into a short. Clearing the
            # stray legs needs a cancel addressed to their owning clientId
            # (BL-20260816-NO-PER-ORDER-IB-CANCEL); until that repair lands,
            # alerting is the honest action and silence is not.
            declared_tp = row["take_profit_1"]
            tp_declared = declared_tp not in (None, 0) and declared_tp > 0
            target_q = cov.get("target_qty")
            if tp_declared and target_q is not None and float(target_q) < size - _IB_COVERAGE_EPSILON:
                summary["target_naked"] = summary.get("target_naked", 0) + 1
                _emit_target_naked_alert(
                    account_id=account_id,
                    symbol=protect_symbol,
                    size=size,
                    target_qty=float(target_q),
                    stop_qty=cov.get("stop_qty"),
                    declared_tp=declared_tp,
                    trade_id=row["id"],
                )
                logger.error(
                    "_check_broker_naked_ib_positions: %s/%s TARGET-NAKED — "
                    "size=%s target_qty=%s stop_qty=%s declared_tp=%s "
                    "trade_id=%s (alert only; re-arm blocked on "
                    "BL-20260816-NO-PER-ORDER-IB-CANCEL)",
                    account_id, protect_symbol, size, target_q,
                    cov.get("stop_qty"), declared_tp, row["id"],
                )
            if covered >= size - _IB_COVERAGE_EPSILON:
                summary["covered"] += 1
                continue  # fully covered (STOP side; see the target check above)
            # Partially covered is the case the old boolean could not see: a
            # surviving sibling leg made the whole netted position read
            # PROTECTED while this trade's own protection was gone.
            if covered > 0:
                summary["partially_naked"] += 1
                logger.warning(
                    "_check_broker_naked_ib_positions: %s/%s PARTIALLY NAKED — "
                    "position %s vs %s covered by %d resting leg(s); "
                    "re-arming trade_id=%s for its own qty",
                    account_id, protect_symbol, size, covered,
                    cov.get("legs", 0), row["id"],
                )
            summary["broker_naked"] += 1
            sl = row["stop_loss"]
            tp = row["take_profit_1"]
            a_sl = sl if (sl not in (None, 0) and sl > 0) else None
            a_tp = tp if (tp not in (None, 0) and tp > 0) else None
            if a_sl is None or a_tp is None:
                r_sl, r_tp = _resolve_protective_levels(
                    db, symbol, str(row["direction"] or "")
                )
                a_sl = a_sl if a_sl is not None else r_sl
                a_tp = a_tp if a_tp is not None else r_tp
            if a_sl is None or a_tp is None:
                logger.warning(
                    "_check_broker_naked_ib_positions: trade_id=%s %s "
                    "broker-naked but no SL/TP resolvable — leaving for alert",
                    row["id"], symbol,
                )
                continue
            if _attempt_naked_autoprotect(row, a_sl, a_tp, db=db):
                summary["rearmed"] += 1
                # Credit ONLY this trade's qty against the netted position's
                # coverage — not the whole position. The old code set the cached
                # verdict to True, which told every sibling on the symbol it was
                # protected when the re-arm had covered just one trade's share;
                # that is the boolean defect in miniature. Crediting the qty
                # keeps a still-uncovered sibling visible on this same sweep.
                try:
                    cov["covered_qty"] = float(cov.get("covered_qty") or 0.0) + float(
                        row["position_size"] or 0.0
                    )
                except (TypeError, ValueError):
                    pass
                logger.info(
                    "_check_broker_naked_ib_positions: re-armed GTC OCA "
                    "(sl=%s tp=%s) on broker-naked trade_id=%s account=%s "
                    "symbol=%s", a_sl, a_tp, row["id"], account_id, symbol,
                )
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            logger.warning(
                "_check_broker_naked_ib_positions: failed for trade_id=%s: %s",
                row["id"], exc,
            )
    if summary["checked"]:
        # One line per sweep that actually looked at something, so the pass is
        # observable even when it changes nothing. Without it the ONLY evidence
        # the sweep runs is a re-arm — i.e. it is visible exactly when something
        # is wrong and invisible when it is working, which is the wrong way
        # round for confirming a deploy.
        logger.info(
            "_check_broker_naked_ib_positions: swept %d open IB position(s) — "
            "covered=%d naked=%d partially_naked=%d target_naked=%d rearmed=%d "
            "read_failed=%d ungradeable=%d errors=%d",
            summary["checked"], summary["covered"], summary["broker_naked"],
            summary["partially_naked"], summary.get("target_naked", 0),
            summary["rearmed"],
            summary["read_failed"], summary["ungradeable"], summary["errors"],
        )
    return summary


def _bybit_sl_leg_qty(leg: dict):
    """Qty a resting Bybit SL leg would close, or ``None`` if unparseable.

    Bybit reports a Partial-mode leg's scoped size on ``qty``; some response
    shapes carry ``triggerQty``. An unknown qty must NOT be silently treated as
    full coverage — the caller counts these and refuses to grade coverage.
    """
    for key in ("qty", "triggerQty", "size"):
        try:
            q = float(leg.get(key))
        except (TypeError, ValueError):
            continue
        if q == q and q > 0:  # not NaN, positive
            return q
    return None


def _bybit_sl_leg_trigger(leg: dict):
    """Price a resting Bybit SL leg would TRIGGER at, or ``None`` if unreadable.

    Sibling of :func:`_bybit_sl_leg_qty`, and deliberately the same shape: a
    conditional order's level is its TRIGGER, not its limit — the same
    distinction IB's STP LMT forces, where reading the limit would compare a
    declared stop against the wrong price.

    ⚠️ ``None``, never ``0.0``. A leg whose trigger cannot be read is one we did
    not grade; a zero would compare against a declared level as a catastrophic
    divergence when the truth is that we could not look.
    """
    for key in ("triggerPrice", "stopPx", "price"):
        try:
            px = float(leg.get(key))
        except (TypeError, ValueError):
            continue
        if px == px and px > 0:  # not NaN, positive
            return px
    return None


def _norm_position_side(raw) -> str:
    """Normalise a direction to ``"long"`` / ``"short"`` (``""`` if unknown).

    Bybit's position rows say ``Buy``/``Sell`` (and ``None``/``""`` when flat)
    while ``trades.direction`` says ``buy``/``sell`` or ``long``/``short``
    depending on the writer. Both sides of the divergence comparison must be on
    one vocabulary or a genuine opposite-side phantom reads as a different
    symbol and is silently skipped.

    Unknown input returns ``""`` — the caller treats that as *ungradeable* and
    declines to judge, never as a match.
    """
    s = str(raw or "").strip().lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return ""


def _bybit_position_protection(client, category: str, symbol: str):
    """Read a Bybit symbol's live protection COVERAGE, mode-agnostically.

    Returns ``None`` on any read failure so the caller SKIPS (never re-arms on
    an unconfirmed read — the fail-safe the Alpaca/IB sweeps also honour), else
    a dict::

        {"size", "side", "covered_qty", "source", "sl_leg_ids",
         "unknown_qty_sl_legs"}

    ``side`` is the netted position's direction normalised to ``"long"`` /
    ``"short"`` (``""`` when flat). Under one-way netting a symbol holds ONE
    position with ONE side, so any open journal row on the OPPOSITE side is a
    phantom by construction — the caller's divergence check needs the side to
    see that, and keying journal qty by symbol alone hides it.

    **Why this measures QUANTITY, not a boolean** (the 2026-07-30 finding).
    This function used to return ``(size, protected)`` with
    ``protected = any(<a resting SL leg exists>)``. Under
    ``BYBIT_TPSL_MODE=partial`` that is wrong in a way that silently loses
    protection: one-way netting means a symbol is ONE exchange position holding
    N journal trades and N qty-scoped legs, and a Partial leg's ``slSize``
    covers only **its own qty**. If some legs are gone — rejected at Bybit's
    20-combined-leg cap (BL-20260721-BYBIT2-XRP-TPSL-LEGCAP), or cancelled when
    a sibling trade closed — the surviving leg still satisfied ``any()``, so
    :func:`_check_broker_naked_bybit_positions` reported PROTECTED and skipped
    while the position was only PARTIALLY covered. The DB-driven
    :func:`_check_naked_positions` cannot see it either (the journal rows keep
    their SL/TP), so a partially-naked netted position was invisible to every
    layer. Live example (2026-07-30): ``bybit_1`` BNBUSDT held 5 netted shorts
    totalling ~13.4 qty with only 2 of 5 rows carrying a tracked leg.

    A Full-mode position-level ``stopLoss`` genuinely covers the whole net
    position, so that path reports ``covered_qty == size``.
    BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT + the 2026-07-30 coverage fix.

    ⚠️ **``covered_qty`` IS SIDE-BLIND, AND IS LEFT THAT WAY DELIBERATELY.**
    It sums EVERY resting SL leg on the symbol regardless of which book the
    leg can actually reduce. Under one-way netting that was harmless (one
    book); since HEDGE mode was armed on this account (2026-08-30,
    ``BYBIT_HEDGE_MODE_SYMBOLS``) a symbol can carry legs for two books and
    they land in one sum. MEASURED on ``bybit_1``/BTCUSDT
    (``/api/diag/bybit_open_orders``, read 2026-09-02T03:30:33Z, trader
    ``git_sha 68e73de8``): position ``Buy 0.018 positionIdx=1`` with legs
    ``Sell 0.018`` (its own) and ``Buy 0.46`` (acting on a short book) —
    ``covered_qty`` 0.478 against a size of 0.018.

    ``leg_side_split`` (:mod:`src.runtime.bybit_leg_sides`) carries the
    per-book breakdown ADDITIVELY. ⚠️ **THE TWO SENTENCES THAT STOOD HERE UNTIL
    2026-09-02 SAID "only the over-cover PAGE reads it" AND "the re-arm decision
    … still reads ``covered_qty``, byte-identically … so nothing about which
    positions get re-armed changed here". BOTH ARE NOW FALSE AND MUST NOT BE
    RE-QUOTED** — they were true of the diagnostic repair (#10739) and stopped
    being true when the Tier-2 change that repair named was made.
    :func:`_check_broker_naked_bybit_positions` now grades coverage through
    ``bybit_leg_sides.graded_book_coverage(leg_side_split)``, so this split
    DECIDES which live positions get a protective stop re-armed.

    That change closed the masking this docstring used to name as open: an
    other-book leg pushing ``covered_qty`` past ``size`` on a position whose own
    stop is gone, which the sweep then skipped as "fully covered". ⚠️ It was
    CONSTRUCTED from the live 2026-09-02T03:30:33Z read above — **n = 1, and no
    live instance of the masking has been observed.**

    ⚠️ ``covered_qty`` ITSELF IS UNCHANGED AND STAYS SIDE-BLIND. It feeds the
    over-cover TRIP, which is the UNION of same-book pile-up and other-book legs
    resting on the symbol; narrowing that to the graded book would make the
    second condition stop tripping and go SILENT — worse than the mislabelling
    #10739 fixed. Only the coverage/re-arm comparison moved.
    """
    try:
        pos_resp = client.get_positions(category=category, symbol=symbol)
        rows = ((pos_resp or {}).get("result") or {}).get("list") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("_bybit_position_protection: get_positions failed %s: %s",
                     symbol, exc)
        return None
    _flat = {
        "size": 0.0, "side": "", "covered_qty": 0.0, "source": "flat",
        "stop_prices": [],
        "sl_leg_ids": set(), "unknown_qty_sl_legs": 0,
    }
    if not rows:
        return _flat  # flat — nothing to protect
    pos = rows[0]
    try:
        size = abs(float(pos.get("size") or 0) or 0.0)
    except (TypeError, ValueError):
        return None
    if size <= 0:
        return _flat
    side = _norm_position_side(pos.get("side"))
    # THE VENUE'S POSITION MODE, carried so the leg-side split can say whether
    # an OPPOSITE book could exist at all (0 = one-way netting ⇒ it cannot;
    # 1/2 = hedge ⇒ it may be a live sibling). Never defaulted to 0 — see
    # `bybit_leg_sides.other_book_state`.
    pos_idx = pos.get("positionIdx")
    # (a) Full-mode position-level stop lives on the position row itself and
    #     genuinely covers the WHOLE net position.
    pos_sl = str(pos.get("stopLoss") or "").strip()
    if pos_sl and pos_sl not in ("0", "0.0", "0.00"):
        # ⚠️ THE *STRING* IS THE WHOLE COVERAGE TEST HERE, and it always was:
        # any non-empty, non-zero stopLoss grades the position FULLY covered,
        # so a stop resting anywhere at all passes
        # (BL-20260820-PROTECTION-COVERAGE-IS-PRICE-BLIND criterion 5). That row
        # was filed on the IB instance, which measured as a 68.8-tick gap worth
        # $1,289.72 on ib_paper MES — and it explicitly left this venue
        # unchecked. It is checked now, and the blast radius is LARGER: the IB
        # instance is a paper account, whereas `bybit_2` is MAINNET.
        #
        # The QUANTITY verdict is unchanged (a Full-mode stop does cover the
        # whole net position); what is added is the PRICE, so a caller holding
        # the journal row can grade where it rests. `stop_prices` is empty when
        # the venue's own value will not parse — *we could not look*, never
        # "the price is fine", and never a fabricated 0.0.
        _px = _bybit_sl_leg_trigger({"price": pos_sl})
        return {
            "size": size, "side": side, "covered_qty": size,
            "source": "full_position_stop",
            "sl_leg_ids": set(), "unknown_qty_sl_legs": 0,
            "stop_prices": [_px] if (_px is not None and _px > 0) else [],
            # ⚠️ None, NOT an empty split. This branch returns BEFORE
            # `get_open_orders` is called, so we did not read the resting legs
            # at all — an empty split would assert "no leg acts on the other
            # book", which is a measurement nobody took. The over-cover branch
            # fires only on `partial_sl_legs`, so no consumer reaches this.
            "leg_side_split": None,
        }
    # (b) Partial-mode SL legs are separate resting conditional orders, each
    #     covering only its own qty — so SUM them and compare against size.
    try:
        oo_resp = client.get_open_orders(
            category=category, symbol=symbol, orderFilter="StopOrder",
        )
        legs = ((oo_resp or {}).get("result") or {}).get("list") or []
    except Exception as exc:  # noqa: BLE001
        logger.debug("_bybit_position_protection: get_open_orders failed %s: %s",
                     symbol, exc)
        return None
    covered = 0.0
    leg_prices: List[float] = []
    leg_ids = set()
    unknown = 0
    # The SL legs themselves, kept so the side split below reads the SAME
    # population the side-blind `covered` sum was built from — a second pass
    # over `legs` would be free to disagree with it.
    sl_legs_seen: List[dict] = []
    # ⚠️ TWO DIFFERENT QUANTITIES, NEVER COLLAPSED (2026-08-26).
    # `covered_qty`/`sl_leg_ids` answer a STOP-COVERAGE question and are
    # deliberately SL-only. `protective_leg_count` answers a CAP-PROXIMITY
    # question, and Bybit's cap is 20 COMBINED TP+SL legs per symbol — so an
    # SL-only count over-states the headroom by however many targets rest.
    #
    # Measured live the day this was added: bybit_1/ETHUSDT held 9 SL and 9 TP
    # legs, i.e. 18 of 20 with TWO left, while the over-cover page (which was
    # fed the SL count) reported "9 of 20 used, 11 left". A safety page whose
    # whole point is cap proximity understated it by 9 legs.
    #
    # This costs NO extra broker call: `orderFilter="StopOrder"` already
    # returns PartialTakeProfit rows alongside PartialStopLoss, and they were
    # being discarded by the SL filter below before anything counted them.
    protective_legs = 0
    for o in legs:
        _kind = str(o.get("stopOrderType") or "").lower()
        if _kind in _SL_LEG_TYPES_MON or _kind in _TP_LEG_TYPES_MON:
            protective_legs += 1
        if _kind not in _SL_LEG_TYPES_MON:
            continue
        sl_legs_seen.append(o)
        oid = o.get("orderId")
        if oid:
            leg_ids.add(str(oid))
        q = _bybit_sl_leg_qty(o)
        if q is None:
            unknown += 1
            continue
        covered += q
        _lpx = _bybit_sl_leg_trigger(o)
        if _lpx is not None:
            leg_prices.append(_lpx)
    return {
        "size": size, "side": side, "covered_qty": covered,
        "source": "partial_sl_legs",
        "sl_leg_ids": leg_ids, "unknown_qty_sl_legs": unknown,
        # Combined TP+SL, for the 20-leg cap only. NOT a coverage figure.
        "protective_leg_count": protective_legs,
        # WHICH BOOK each SL leg acts on (2026-09-02). ⚠️ THIS IS AN ORDER-PATH
        # INPUT: `_check_broker_naked_bybit_positions` grades coverage through
        # `bybit_leg_sides.graded_book_coverage` over this split, so a
        # misclassification here changes which live positions get re-armed.
        # `covered_qty` above stays SIDE-BLIND on purpose — it feeds the
        # over-cover TRIP (a union of two conditions) and the page, never the
        # re-arm. Do not "harmonise" the two.
        "leg_side_split": _bybit_leg_sides.split_legs_by_side(
            pos.get("side"), sl_legs_seen,
            qty_of=_bybit_sl_leg_qty, position_idx=pos_idx,
        ),
        # A Partial-mode SL leg is a conditional order, so its level is the
        # TRIGGER price, not the limit — the same distinction IB's STP LMT
        # forces. Sorted for a stable read; empty means no leg carried a
        # readable trigger, which is not "the prices are fine".
        "stop_prices": sorted(leg_prices),
    }


def _bybit_mark_fully_covered(state: dict, size: float) -> dict:
    """In-tick idempotency marker: this symbol has just been re-protected.

    A netted symbol holds MANY journal rows and ONE exchange position, so after
    a top-up or a Full-mode re-arm the remaining rows for that symbol must not
    each fire another one. The sweep has always done this by rewriting the
    cached ``covered_qty`` to ``size``; since 2026-09-02 the re-arm decision
    reads the GRADED-book figure instead, so the split has to say full too or
    the marker would stop working and every sibling row would re-arm again.

    ⚠️ The returned dict is a CACHE MARKER, not a venue reading, and it never
    replaces the read: it is built per-tick and thrown away. The side-blind
    ``covered_qty`` is set the same way it always was.
    """
    marked = {**state, "covered_qty": size}
    split = state.get("leg_side_split")
    if isinstance(split, dict):
        marked["leg_side_split"] = {
            **split,
            f"{_bybit_leg_sides.LEG_REDUCES_GRADED_BOOK}_qty": size,
        }
    return marked


def _bybit_top_up_partial_sl(acc: dict, symbol: str, row, uncovered_qty, sl) -> bool:
    """Add a qty-scoped Partial SL leg covering exactly *uncovered_qty*.

    The precise remedy for a PARTIALLY-covered netted Bybit position: rather
    than stamping one trade's levels over the whole position (what a Full-mode
    re-arm does), add a single leg for just the qty that has lost its own leg,
    at *sl*. Trades that still hold a live leg keep the geometry they chose.

    Routes through ``execute.modify_open_order`` with ``sl_order_id=None`` so it
    takes that function's legacy add-a-leg branch
    (``set_trading_stop(tpslMode="Partial", slSize=<qty>)``) — the one sanctioned
    order path; this function never talks to the venue directly. TP is
    deliberately omitted: a missing take-profit is not a risk, and adding one
    would burn a second leg against Bybit's 20-leg cap.

    Returns True only on a confirmed OK. Best-effort — any failure returns False
    so the caller falls back to the whole-position Full-mode re-arm rather than
    leaving the gap open. 2026-07-30 coverage fix.
    """
    try:
        qty = float(uncovered_qty)
    except (TypeError, ValueError):
        return False
    if qty <= 0 or sl in (None, 0):
        return False
    try:
        from src.units.accounts.clients import bybit_client_for
        from src.units.accounts.execute import modify_open_order

        client = bybit_client_for(acc)
        if client is None:
            return False
        resp = modify_open_order(
            client, acc, symbol=symbol, sl=float(sl), tp=None, qty=qty,
            sl_order_id=None, tp_order_id=None,
        )
        ok = bool((resp or {}).get("ok"))
        if not ok:
            logger.warning(
                "_bybit_top_up_partial_sl: refused for %s/%s uncovered=%s: %r",
                acc.get("account_id"), symbol, qty, (resp or {}).get("error"),
            )
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_bybit_top_up_partial_sl: failed for %s/%s uncovered=%s: %s",
            acc.get("account_id"), symbol, uncovered_qty, exc,
        )
        return False



# ---------------------------------------------------------------------------
# Netting partial-close ATTRIBUTION
# (BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED;
#  design: docs/netting-partial-close-attribution-DESIGN.md)
# ---------------------------------------------------------------------------

#: Observation state for the 2-observation confirm, keyed
#: ``(account, symbol, direction)`` → ``(first_seen_monotonic, excess_qty,
#: first_seen_iso)``. The ISO stamp is kept because it — not "now" — is what
#: the exit anchor is taken at.
#: In-process: a restart re-arms from scratch, which is fail-SAFE here (the
#: worst case is a delayed attribution, never an early close).
_NETTING_DIVERGENCE_SEEN: Dict[tuple, tuple] = {}


def _netting_attribution_mode() -> str:
    """``annotate`` (default) or ``apply``.

    A ``*_MODE`` var, not a default-off ``*_ENABLED`` gate — the same shape
    ``NEWS_INFLUENCE_MODE`` / ``CONVICTION_SIZING_MODE`` use for a NEW apply
    path, and the shape ``env-gate-guard`` accepts. It is NOT hiding a required
    capability: at ``annotate`` the reconciler still does all the work and
    records the exact rows it WOULD reduce, it just does not write the money DB.
    That staging is operator decision 4 (design packet §5) — prove the row list
    on ``bybit_1`` before touching ``bybit_2`` real money.
    """
    raw = str(os.environ.get("NETTING_ATTRIBUTION_MODE", "") or "").strip().lower()
    return "apply" if raw == "apply" else "annotate"


def _netting_attribution_accounts() -> set:
    """Optional allowlist (CSV) of accounts that may be **written**. Empty = all.

    Scopes the WRITE, never the measurement. Every Bybit account is observed and
    annotated to the soak log regardless; only an allowlisted one can have
    ``apply`` actually touch the money DB.

    That split is the entire point and it was wrong until 2026-08-09, when the
    allowlist intersected the account set at the top of the pass
    (``bybit_ids &= allow``). Operator decision 4 asked to stage the WRITE on
    ``bybit_1`` before ``bybit_2`` — a correct instruction — but scoping the
    whole pass also switched off *observation* of every other account. So while
    the allowlist was set, real-money ``bybit_2`` was not merely un-written, it
    was **invisible**: no divergence check, no soak row, nothing to review before
    widening the allowlist to it. It had been measured non-clean on 2026-08-06.

    A staging control that silently disables measurement of the thing you are
    staging toward is self-defeating, and this is the same defect the
    gross-exposure ceiling had (``docs/design/gross-exposure-governance-DESIGN.md``
    § 3): *conflating "no policy here" with "no data here."*
    """
    raw = str(os.environ.get("NETTING_ATTRIBUTION_ACCOUNTS", "") or "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def _netting_may_write(account_id: str, mode: str, allow: set) -> bool:
    """May the reconciler write the money DB for this account?

    The ONLY place the allowlist is consulted. Both conditions must hold: the
    global mode is ``apply`` AND (no allowlist is set, or this account is on it).
    """
    if mode != "apply":
        return False
    return (not allow) or (account_id in allow)


def _netting_rows_to_attribute(rows, excess, live_leg_ids):
    """Pick which open journal rows account for *excess* qty of over-claim.

    Returns ``[(row, qty_to_remove, basis)]`` where ``basis`` is the honest
    label for HOW the row was selected:

    * ``"leg_gone"`` — the row's tracked Bybit SL leg is no longer resting, so
      its own protection fired or was cancelled. This is **evidence, not
      proof**: leg absence cannot distinguish "fired" from "cancelled", so it
      still yields an ESTIMATED close, never MEASURED. It is preferred over
      FIFO because it names a specific trade rather than assuming an order.
    * ``"fifo"`` — no tracked leg to go on, so oldest-first. A defensible
      convention, and labelled as one.

    Option (b) with (a) as the declared fallback (design packet §3). (b) alone
    under-covers by construction — 16/19 open Bybit rows carry a tracked leg
    (measured 2026-08-06), and one W1 receipt had a DEAD tracked id — so FIFO
    is not an alternative to it, it is its remainder.

    **A row whose tracked leg is still RESTING is picked last**, after the
    untracked ones. A live protective leg is positive evidence that the
    exchange still holds that row's qty, so attributing a close to it while an
    untracked row is available would be choosing the *less* likely candidate.
    Plain oldest-first over the whole set gets this wrong whenever the oldest
    row happens to be a tracked-and-still-protected one.
    """
    picked = []
    remaining = float(excess)
    eps = 1e-9

    def _take(row, basis):
        nonlocal remaining
        if remaining <= eps:
            return
        try:
            qty = abs(float(row["position_size"]))
        except (TypeError, ValueError):
            return
        if qty <= 0:
            return
        take = min(qty, remaining)
        picked.append((row, take, basis))
        remaining -= take

    # (b) rows whose tracked leg is gone, oldest-first among themselves.
    leg_gone = [
        r for r in rows
        if str(r["sl_order_id"] or "") and str(r["sl_order_id"]) not in live_leg_ids
    ]
    for row in sorted(leg_gone, key=lambda r: str(r["created_at"] or "")):
        _take(row, "leg_gone")

    # (a) FIFO the residual — UNTRACKED rows first, then rows whose leg is
    #     still resting (see the docstring: a live leg is evidence AGAINST
    #     that row having closed, so it is the last candidate, not a
    #     position-in-line determined by age alone).
    already = {id(r) for r, _, _ in picked}
    rest = [r for r in rows if id(r) not in already]
    rest.sort(key=lambda r: (
        1 if str(r["sl_order_id"] or "") in live_leg_ids else 0,
        str(r["created_at"] or ""),
    ))
    for row in rest:
        _take(row, "fifo")

    return picked


def _is_pairs_sleeve_row(row) -> bool:
    """Is this open journal row owned by the isolated pairs executor?

    ONE definition, used by BOTH the netting reconciler (which refuses to
    attribute these — `pairs_executor` holds its own state) and the divergence
    sweep (which must say so rather than printing a generic "phantom row"
    ERROR no reconciler will ever act on). Two copies of this predicate could
    drift into disagreeing about who owns a row, which is precisely the seam
    the alarm came from.
    """
    try:
        setup = str(row["setup_type"] or "")
        strat = str(row["strategy_name"] or "")
    except (KeyError, IndexError, TypeError):
        return False
    return setup.startswith("pairs") or strat.startswith("pairs")


def _reconcile_netting_partial_closes(db) -> Dict[str, int]:
    """Attribute a netted partial close to the journal rows it actually reduced.

    THE DEFECT (BL-20260801). Under Bybit one-way netting a symbol is ONE
    exchange position holding N journal rows. A position-level exit shrinks that
    single position, but close detection is per-ORDER —
    :func:`_reconcile_open_trades` reconciles each row against *its own* Bybit
    order — so a sibling row whose order filled long ago has no order event to
    observe and keeps its full ``position_size`` indefinitely. Measured
    2026-08-06: ``bybit_1`` SOLUSDT journal 2075.2 vs exchange 4.6 (**451x**),
    and every non-Bybit venue is exactly clean because they do not net.

    PROVENANCE (operator directive 2026-08-06). Prefer an ESTIMATE with a stated
    anchor over declaring nothing — but an estimate REQUIRES an anchor, or it is
    FABRICATED, which is the class that produced the phantom -$6,358 leak. So
    the ladder is:

    * anchored bar at the divergence's FIRST OBSERVATION → ESTIMATED
      (``exit_anchor.ANCHOR_SOURCE``). Going forward that observation is within
      one monitor tick of the real close, which is what makes it defensible.
    * venue asked and has no bar → ``UNMEASURED_MARKER``. Declared, not guessed.
    * budget spent / transient read failure → **retry**, declare nothing.

    ``first observed`` is deliberately NOT "now": anchoring a row that may have
    closed days ago to the current bar is precisely the sweep-time-mark
    fabrication that :func:`_sweep_local_pnl_for_unpriced` was fixed to stop.
    For the pre-existing backlog the first observation is only an UPPER BOUND on
    the close, and the row records that (``netting_anchor_basis``) so nobody
    later mistakes it for a measurement.

    MODE. ``annotate`` (default) does every step and writes the decision to
    ``runtime_logs/netting_attribution_soak.jsonl`` WITHOUT touching the money
    DB; ``apply`` also closes the rows. Staged per operator decision 4.

    Fail-safe throughout: an unreadable exchange position is SKIPPED (never
    attribute on an unconfirmed read); pairs-sleeve rows are excluded (that
    executor owns its own state); the 2-observation confirm reuses
    ``RECONCILER_CLOSE_CONFIRM_SECONDS``. Never raises.
    """
    summary: Dict[str, int] = {
        "checked": 0, "divergent": 0, "pending_confirm": 0, "rows_selected": 0,
        "closed": 0, "annotated": 0, "declared_unmeasured": 0,
        "apply_suppressed_by_allowlist": 0,
        "deferred_anchor": 0, "skipped_unreadable": 0, "skipped_pairs": 0,
        "errors": 0,
    }
    mode = _netting_attribution_mode()
    allow = _netting_attribution_accounts()

    try:
        from src.bot import data_loaders
        from src.units.accounts.clients import bybit_client_for
        from src.units.accounts.execute import _bybit_category
        from src.runtime.exit_anchor import ANCHOR_SOURCE, AnchorBudget, bar_close_at
        from src.runtime.provenance import UNMEASURED_MARKER

        accounts = data_loaders.list_accounts() or []
        bybit_ids = {
            str(a.get("account_id")) for a in accounts
            if str(a.get("exchange", "")).lower() == "bybit"
        }
        # NOTE: `allow` is deliberately NOT intersected here. It scopes the
        # WRITE (see _netting_may_write), not the pass — observing every Bybit
        # account is what makes widening the allowlist an evidence-based
        # decision rather than a blind one.
        if not bybit_ids:
            return summary
        acc_by_id = {str(a.get("account_id")): a for a in accounts}

        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, position_size, "
                "       entry_price, created_at, setup_type, strategy_name, "
                "       sl_order_id, notes "
                "  FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_reconcile_netting_partial_closes: read failed: %s", exc)
        summary["errors"] += 1
        return summary

    # Group the open rows by (account, symbol, direction), excluding the pairs
    # sleeve — `pairs_executor` is an ISOLATED 2-leg order path with its own
    # state, and closing its rows behind its back would desync it (design
    # packet §4 constraint 2).
    by_key: Dict[tuple, list] = {}
    for row in rows:
        aid = str(row["account_id"] or "")
        if aid not in bybit_ids:
            continue
        if _is_pairs_sleeve_row(row):
            summary["skipped_pairs"] += 1
            continue
        sym = str(row["symbol"] or "").upper()
        direction = _norm_position_side(row["direction"])
        if not sym or not direction:
            continue
        by_key.setdefault((aid, sym, direction), []).append(row)

    now_mono = time.monotonic()
    now_iso = datetime.now(timezone.utc).isoformat()
    confirm_s = _close_confirm_seconds()
    anchor_budget = AnchorBudget(_exit_anchor_fetches_per_tick())
    clients: Dict[str, object] = {}
    protection: Dict[tuple, object] = {}

    for (aid, sym, direction), key_rows in sorted(by_key.items()):
        summary["checked"] += 1
        try:
            if is_active_close(aid, sym):
                continue  # an active close is in flight — let it settle
            cache_key = (aid, sym)
            if cache_key not in protection:
                acc = acc_by_id.get(aid)
                if acc is None:
                    continue
                if aid not in clients:
                    clients[aid] = bybit_client_for(acc)
                client = clients[aid]
                if client is None:
                    continue
                protection[cache_key] = _bybit_position_protection(
                    client, _bybit_category(acc), sym,
                )
            state = protection[cache_key]
            if state is None:
                # Could-not-read. NEVER attribute on an unconfirmed read — the
                # same fail-safe the three naked sweeps honour.
                summary["skipped_unreadable"] += 1
                continue

            size = float(state["size"])
            exch_side = str(state.get("side") or "")
            if size > 0 and not exch_side:
                # Live position with an unreadable side: we cannot say which
                # rows it backs, so we may not grade them.
                summary["skipped_unreadable"] += 1
                continue
            backed = size if (size > 0 and exch_side == direction) else 0.0

            journal_qty = 0.0
            for r in key_rows:
                try:
                    journal_qty += abs(float(r["position_size"]))
                except (TypeError, ValueError):
                    continue
            excess = journal_qty - backed
            if excess <= max(backed, journal_qty) * _BYBIT_QTY_DIVERGENCE_FRAC:
                _NETTING_DIVERGENCE_SEEN.pop((aid, sym, direction), None)
                continue
            summary["divergent"] += 1

            # 2-observation confirm. One reading of a smaller position is not
            # proof — a just-placed order can read absent while pending fill
            # (the Alpaca RECONCILER_SNAPSHOT_MIN_FILL_AGE_S incident shape).
            seen = _NETTING_DIVERGENCE_SEEN.get((aid, sym, direction))
            if seen is None:
                _NETTING_DIVERGENCE_SEEN[(aid, sym, direction)] = (
                    now_mono, excess, now_iso,
                )
                summary["pending_confirm"] += 1
                continue
            first_mono, _first_excess, first_seen_iso = seen
            if (now_mono - float(first_mono)) < max(confirm_s, 0.0):
                summary["pending_confirm"] += 1
                continue
            picked = _netting_rows_to_attribute(
                key_rows, excess, set(state.get("sl_leg_ids") or ()),
            )
            summary["rows_selected"] += len(picked)

            anchored, anchor_status = bar_close_at(
                sym, first_seen_iso, budget=anchor_budget,
            )
            if anchor_status == "deferred":
                # We did NOT look. Retry next tick; declare nothing.
                summary["deferred_anchor"] += 1
                continue

            # The EFFECTIVE mode for this account, which is what the soak row
            # must record. Stamping the global `mode` would put "apply" on rows
            # of a non-allowlisted account where nothing was applied — an audit
            # trail that describes an action the code did not take, which is the
            # sub-class-A diagnostic-provenance defect and exactly the kind of
            # confident-wrong record this soak exists to be trusted as.
            may_write = _netting_may_write(aid, mode, allow)
            eff_mode = "apply" if may_write else "annotate"
            apply_scope = (
                "no_allowlist" if not allow
                else ("allowlisted" if aid in allow else "not_allowlisted")
            )

            for row, take, basis in picked:
                _netting_soak_row(
                    account_id=aid, symbol=sym, direction=direction,
                    row=row, take=take, basis=basis, mode=eff_mode,
                    journal_qty=journal_qty, exchange_qty=backed,
                    anchor_status=anchor_status, anchor_price=anchored,
                    anchored_at=first_seen_iso,
                    global_mode=mode, apply_scope=apply_scope,
                )
                summary["annotated"] += 1
                if not may_write:
                    if mode == "apply":
                        # Visible, not silent: under a global `apply` this row
                        # was held back purely by the allowlist. Counting it
                        # keeps "staged" distinguishable from "nothing to do".
                        summary["apply_suppressed_by_allowlist"] += 1
                    continue
                closed_ok = _netting_apply_close(
                    db, row=row, take=take, basis=basis,
                    anchor_price=anchored, anchor_status=anchor_status,
                    anchored_at=first_seen_iso,
                    anchor_source=ANCHOR_SOURCE,
                    unmeasured_marker=UNMEASURED_MARKER,
                )
                if closed_ok == "closed":
                    summary["closed"] += 1
                elif closed_ok == "unmeasured":
                    summary["declared_unmeasured"] += 1
                else:
                    summary["errors"] += 1
        except Exception as exc:  # noqa: BLE001 — one bad key never aborts the sweep
            logger.warning(
                "_reconcile_netting_partial_closes: %s/%s/%s raised: %s",
                aid, sym, direction, exc,
            )
            summary["errors"] += 1

    return summary


def _netting_soak_row(
    *, account_id, symbol, direction, row, take, basis, mode,
    journal_qty, exchange_qty, anchor_status, anchor_price, anchored_at,
    global_mode=None, apply_scope=None,
) -> None:
    """Append one attribution DECISION to the observe-only soak log.

    Written in BOTH modes, so ``annotate`` produces the exact row list the
    operator reviews before the ``apply`` flip, and ``apply`` leaves the same
    audit trail next to the money-DB write. Best-effort — the soak never blocks
    the reconciler.

    ``mode`` is the **effective** mode for THIS account — what actually happened
    to this row. ``global_mode`` + ``apply_scope`` record why it was effective:
    a row reading ``mode: annotate, global_mode: apply,
    apply_scope: not_allowlisted`` says "this WOULD have been written; only the
    allowlist held it back", which is precisely the evidence an operator needs
    to decide whether to widen the allowlist. Collapsing the two into one field
    would make a staged account indistinguishable from a globally-annotating
    one, and would stamp ``apply`` on rows nothing was applied to.
    """
    try:
        from src.utils.paths import runtime_logs_dir

        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            # Effective (what happened to this row) vs global (what was asked
            # for) vs why they differ. Three fields because they are three
            # facts; see the docstring.
            "mode": mode,
            "global_mode": global_mode if global_mode is not None else mode,
            "apply_scope": apply_scope,
            "account_id": account_id,
            "symbol": symbol,
            "direction": direction,
            "trade_id": row["id"],
            "strategy": row["strategy_name"],
            "row_qty": _safe_float(row["position_size"]),
            "attributed_qty": round(float(take), 10),
            # HOW this row was selected — never conflated with how its price
            # was derived. `leg_gone` is evidence (a tracked SL leg is no
            # longer resting), not proof that it fired.
            "attribution_basis": basis,
            "journal_qty": round(float(journal_qty), 10),
            "exchange_qty": round(float(exchange_qty), 10),
            "excess_qty": round(float(journal_qty) - float(exchange_qty), 10),
            # The anchor's own status is the provenance, verbatim from
            # exit_anchor — `anchored` → ESTIMATED, `no_anchor` → declared
            # unmeasured. Never collapsed into a single boolean.
            "anchor_status": anchor_status,
            "anchor_price": anchor_price,
            "anchored_at": anchored_at,
            "anchor_basis": "divergence_first_observed",
        }
        path = runtime_logs_dir() / "netting_attribution_soak.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception as exc:  # noqa: BLE001  # allow-silent: observe-only soak row; losing one line must never block the attribution reconciler or the monitor tick. Logged.
        logger.debug("_netting_soak_row: write failed: %s", exc)


def _netting_apply_close(
    db, *, row, take, basis, anchor_price, anchor_status, anchored_at,
    anchor_source, unmeasured_marker,
) -> str:
    """Close (or partially reduce) one row. Returns ``closed``/``unmeasured``/``error``.

    Provenance is the whole point of this function, so it is explicit:

    * ``anchor_status == "anchored"`` → price the close from the anchored bar
      and stamp ``exit_price_source = ANCHOR_SOURCE`` (**ESTIMATED**). Per the
      operator directive 2026-08-06, an estimate with a stated anchor beats
      declaring nothing — provided the anchor is real.
    * ``anchor_status == "no_anchor"`` → the venue was asked and has no bar.
      Close carrying ``UNMEASURED_MARKER`` rather than substituting a price.
      That is not a lesser effort, it is the only honest terminal state; an
      anchorless "estimate" would be FABRICATED, the exact class behind the
      phantom -$6,358 leak.

    ``netting_attribution_basis`` records that the row was selected by
    attribution rather than observed closing, so no later consumer mistakes an
    inferred close for a broker-confirmed one.
    """
    try:
        # Same module the sibling `_sweep_local_pnl_for_unpriced` uses. THREE
        # `contract_value_usd_for` definitions exist in the tree
        # (local_pnl / accounts.risk / profile_loader) — pricing a close with a
        # different multiplier than the sweep would silently disagree with it.
        from src.runtime.local_pnl import (
            compute_pnl_percent, compute_realized_pnl, contract_value_usd_for,
        )

        notes = _decode_notes(row["notes"])
        qty = abs(_safe_float(row["position_size"]) or 0.0)
        entry = _safe_float(row["entry_price"])
        partial = float(take) < qty - 1e-9

        notes["netting_attribution_basis"] = basis
        notes["netting_attributed_qty"] = round(float(take), 10)
        notes["netting_anchor_basis"] = "divergence_first_observed"
        notes["netting_anchored_at"] = anchored_at

        if partial:
            # The exchange only gave back PART of this row. Reduce it and leave
            # it open — inventing a close for the remainder would be a second
            # fabrication on top of the first.
            updates = {
                "position_size": round(qty - float(take), 10),
                "notes": dump_capped(notes, 500),
            }
            db.update_trade(int(row["id"]), updates)
            return "closed"

        # package-cascade: KNOWN GAP — this full-close branch closes the trade
        # and never touches the linked `order_packages` row at all (this function
        # has no package reference of any kind), so the second-line
        # `_sweep_stuck_linked_packages` force-closes it a tick later as
        # 'stuck_cascade_recovered' and fires `enqueue_stuck_package_sweep`.
        # Measured 2026-08-22: `netting_attributed` is 5.3% of main-path closes,
        # so this fires in production. Tier-2 to fix; filed as
        # BL-20260822-SEVEN-OF-EIGHT-TRADE-CLOSE-SITES-DO-NOT-CASCADE-THEIR-PACKAGE.
        updates: Dict[str, Any] = {
            "status": "closed",
            # `trades` has exit_reason; close_reason lives on `order_packages`.
            # Writing the sibling table's column name made every FULL close
            # raise "no such column: close_reason" — the partial branch below
            # only touches position_size/notes, so it worked and hid this.
            # Live-caught 2026-08-08 the first time apply was ever enabled;
            # annotate never calls update_trade, so no soak could surface it.
            "exit_reason": "netting_attributed",
            "closed_at": anchored_at,
            "notes": None,  # replaced below once provenance is decided
        }
        if anchor_status == "anchored" and anchor_price and anchor_price > 0:
            cvu = contract_value_usd_for(str(row["symbol"] or ""))
            pnl = compute_realized_pnl(
                entry_price=entry, exit_price=float(anchor_price),
                qty=qty, direction=row["direction"], contract_value_usd=cvu,
            )
            notes["pnl_source"] = "local_compute"
            notes["exit_price_source"] = anchor_source
            notes["contract_value_usd"] = cvu
            notes.pop("unmeasured_reason", None)
            updates["exit_price"] = float(anchor_price)
            if pnl is not None:
                updates["pnl"] = pnl
                pct = compute_pnl_percent(
                    pnl=pnl, entry_price=entry, qty=qty, contract_value_usd=cvu,
                )
                if pct is not None:
                    updates["pnl_percent"] = pct
            updates["notes"] = dump_capped(notes, 500)
            db.update_trade(int(row["id"]), updates)
            return "closed"

        # no_anchor — declare, never substitute.
        notes["pnl_source"] = unmeasured_marker
        notes["unmeasured_reason"] = "netting_attribution_no_anchor"
        updates["notes"] = dump_capped(notes, 500)
        db.update_trade(int(row["id"]), updates)
        return "unmeasured"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_netting_apply_close: trade %s failed: %s", row["id"], exc,
        )
        return "error"


def _check_broker_naked_bybit_positions(db) -> Dict[str, int]:
    """Re-arm protection on Bybit positions that are NAKED at the broker.

    The Bybit analogue of :func:`_check_broker_naked_equity_positions` /
    :func:`_check_broker_naked_ib_positions`
    (BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT). The DB-driven
    :func:`_check_naked_positions` only flags a row whose *journal* SL/TP is
    missing — but a Bybit position keeps its journal SL/TP (written from the
    intended levels at open) while its actual broker protection is gone: under
    ``BYBIT_TPSL_MODE=partial`` the qty-scoped legs desync from the netted
    one-way position (intent_reduce legs add none; a close cancels its own
    trade's legs; the 20-leg cap can silently block an amend), so a broker-naked
    Bybit position is invisible to that check. The earlier design assumed Bybit
    "attaches SL/TP atomically at entry, so a naked orphan can't occur" — the
    real-money bybit_2 XRPUSDT no-bracket incident (2026-07-29) disproved it.

    For each open, past-grace Bybit position this asks the broker whether the
    net position actually carries a stop (Full-mode position stopLoss OR a
    resting Partial SL leg — :func:`_bybit_position_protection`) and, when none
    does, re-arms a Full-mode position-level bracket via
    :func:`_attempt_naked_autoprotect` (levels from the journal row, or the
    originating order package as a fallback). The broker's own protection state
    IS the idempotency; a read failure is skipped; an actively-closing symbol is
    skipped (mirrors the Alpaca sweep's close-vs-rearm guard). Never raises.

    ⚠️ **COVERAGE IS GRADED AGAINST THE BOOK THIS POSITION IS ON** (2026-09-02).
    A protective leg is reduce-only, so it acts on the book it can SHRINK.
    ``_bybit_position_protection``'s ``covered_qty`` sums EVERY resting SL leg
    on the symbol regardless of side, and since HEDGE mode was armed on
    ``bybit_1``/``bybit_2`` (2026-08-30) a symbol can carry legs for TWO books
    in that one sum — so an OTHER-book leg can push the total past ``size`` on a
    position whose OWN stop is gone, and this sweep would skip a genuinely naked
    position as "fully covered". The re-arm/top-up decision therefore reads
    ``bybit_leg_sides.graded_book_coverage(leg_side_split)``; ``covered_qty``
    still feeds the over-cover TRIP, which is deliberately the side-blind UNION
    of same-book pile-up AND other-book legs — narrowing that would make the
    other-book case go silent.

    ⚠️ CONSTRUCTED from the live 2026-09-02T03:30:33Z read — **n = 1, and no
    live instance of the masking has been observed.**

    An UNGRADEABLE side split (``graded_book_coverage`` returns ``None``)
    refuses in BOTH directions: no re-arm, and the side-blind sum is not banked
    as coverage either. It is counted (``coverage_side_ungradeable``) and
    logged, so *we could not look* is a reportable condition rather than a
    silent skip — the same posture the unparseable-leg-qty guard already takes.

    Returns ``{"checked", "broker_naked", "rearmed", "errors"}``.
    """
    summary: Dict[str, int] = {
        "checked": 0, "broker_naked": 0, "rearmed": 0, "errors": 0,
        # 2026-07-30 coverage fix: a netted position can be PARTIALLY
        # covered (some per-trade qty-scoped legs lost). Counted and
        # remediated separately from a fully-naked position.
        "partially_naked": 0, "topped_up": 0, "unconfirmed": 0,
        # Detect-only anomalies found live by bybit-bracket-audit 2026-07-30:
        # resting legs summing far over the position (accumulation), and open
        # journal qty exceeding the netted exchange size (phantom rows).
        "over_covered": 0, "over_cover_alerted": 0,
        # Split OUT of `over_covered`, never folded into it: an over-cover trip
        # whose excess sits on the OTHER book is a different condition with a
        # different remedy, and pooling the two is what the 2026-09-02 fix
        # exists to stop. `over_cover_split_ungraded` is *we could not tell
        # which* — emphatically not a third finding, and not a clean read.
        "over_cover_other_book": 0, "over_cover_split_ungraded": 0,
        # The re-arm decision could not be graded because the leg/position SIDE
        # split was incomplete. Counted SEPARATELY from `unconfirmed` (an
        # unparseable leg QTY): both refuse, but they refuse for different
        # reasons and a reader chasing one must not find the other's rows.
        "coverage_side_ungradeable": 0,
        "journal_qty_divergent": 0,
        "journal_qty_divergent_pairs": 0,
    }
    try:
        from src.bot import data_loaders
        from src.units.accounts.clients import bybit_client_for
        from src.units.accounts.execute import _bybit_category

        accounts = data_loaders.list_accounts() or []
        bybit_ids = {
            str(a.get("account_id"))
            for a in accounts
            if str(a.get("exchange", "")).lower() == "bybit"
        }
        if not bybit_ids:
            return summary
        acc_by_id = {str(a.get("account_id")): a for a in accounts}

        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, position_size, "
                "stop_loss, take_profit_1, created_at, notes "
                "FROM trades WHERE status='open' AND COALESCE(is_backtest,0)=0"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_check_broker_naked_bybit_positions: read failed: %s", exc)
        summary["errors"] += 1
        return summary

    now = datetime.now(timezone.utc)
    clients: Dict[str, object] = {}
    # One protection read per (account, symbol) per tick — a netted symbol has
    # many journal rows but one exchange position.
    protection_cache: Dict[tuple, object] = {}
    # Symbol-level anomaly checks run ONCE per (account, symbol), not per row.
    # Two INDEPENDENT guard sets — the divergence check and the leg-accumulation
    # check fire at different points in the loop (divergence runs even when the
    # exchange is flat; accumulation needs a live position), so sharing one set
    # would let whichever runs first suppress the other.
    anomaly_checked: set = set()
    overcover_checked: set = set()
    # Sum of open journal qty per (account, symbol, DIRECTION) — compared
    # against the netted exchange position to surface phantom open rows.
    #
    # Keyed by direction since 2026-08-06. It was keyed by (account, symbol)
    # alone, which pools the two sides: under one-way netting only ONE side can
    # be live, so an open row on the opposite side is a phantom by
    # construction — yet pooling let it CANCEL OUT against a genuine excess on
    # the live side and read clean. That is the phantom case itself.
    journal_qty_by_key: Dict[tuple, float] = {}
    pairs_qty_by_key: Dict[tuple, float] = {}
    # (No separate symbol set is needed: the sweep loop below iterates the open
    # JOURNAL rows, so a symbol whose exchange position is flat is still
    # visited — it just has no protection work to do. That is exactly why the
    # divergence check can now grade it.)
    for row in rows:
        _aid = str(row["account_id"] or "")
        _sym = str(row["symbol"] or "").upper()
        if not _aid or not _sym or _aid not in bybit_ids:
            continue
        try:
            _q = abs(float(row["position_size"]))
        except (TypeError, ValueError):
            continue
        _dir = _norm_position_side(row["direction"])
        journal_qty_by_key[(_aid, _sym, _dir)] = (
            journal_qty_by_key.get((_aid, _sym, _dir), 0.0) + _q
        )
        # How much of this key is owned by the PAIRS SLEEVE. The netting
        # reconciler deliberately refuses pairs rows (`skipped_pairs`) because
        # `pairs_executor` is an isolated 2-leg path holding its own state — so
        # a divergence made of pairs rows has NO reconciler that will ever
        # attribute it. Detecting it and printing the generic message anyway
        # produced a permanent ERROR every ~5 min that nobody could act on
        # (bybit_1/BNBUSDT, live-confirmed 2026-08-08), and an alarm that is
        # routinely walked past is itself the P1 (CLAUDE.md § "If you see
        # something, say something"). Same predicate as the reconciler's, so
        # the two cannot disagree about what "a pairs row" is.
        if _is_pairs_sleeve_row(row):
            pairs_qty_by_key[(_aid, _sym, _dir)] = (
                pairs_qty_by_key.get((_aid, _sym, _dir), 0.0) + _q
            )
    for row in rows:
        account_id = str(row["account_id"] or "")
        if account_id not in bybit_ids:
            continue
        summary["checked"] += 1
        created = _parse_created_at(row["created_at"])
        if (
            created is not None
            and (now - created).total_seconds() < _NAKED_POSITION_GRACE_SECONDS
        ):
            continue  # fresh fill may not have propagated; let the entry settle
        symbol = str(row["symbol"] or "")
        if not symbol:
            continue
        if is_active_close(account_id, symbol):
            continue  # an active close is in flight this tick — let it flatten
        try:
            acc = acc_by_id[account_id]
            if account_id not in clients:
                clients[account_id] = bybit_client_for(acc)
            client = clients[account_id]
            if client is None:
                continue
            category = _bybit_category(acc)
            cache_key = (account_id, symbol.upper())
            if cache_key not in protection_cache:
                protection_cache[cache_key] = _bybit_position_protection(
                    client, category, symbol,
                )
            state = protection_cache[cache_key]
            if state is None:
                continue  # read failure — never re-arm on an unconfirmed read
            size = float(state["size"])
            covered = float(state["covered_qty"])
            exch_side = str(state.get("side") or "")

            # ---- JOURNAL/BROKER qty divergence (detect-only) -----------------
            # Runs BEFORE the flat-skip below. Until 2026-08-06 this check sat
            # AFTER `if size <= 0: continue`, so the case it exists to catch —
            # exchange FLAT while journal rows are still open, i.e. MAXIMAL
            # divergence, the W1 155x finding — returned clean because the
            # symbol was skipped before anything was compared. An empty result
            # read as a clean negative (CLAUDE.md, diagnostic provenance
            # sub-class C). Detect-only: no remediation here, by design —
            # attribution is the Tier-2 netting fix
            # (BL-20260801-NETTING-PARTIAL-CLOSE-ROWS-NEVER-REDUCED,
            # docs/netting-partial-close-attribution-DESIGN.md).
            if cache_key not in anomaly_checked:
                anomaly_checked.add(cache_key)
                _j_long = journal_qty_by_key.get((*cache_key, "long"), 0.0)
                _j_short = journal_qty_by_key.get((*cache_key, "short"), 0.0)
                # Qty the exchange actually backs, per side. A flat position
                # backs nothing on EITHER side; a live one backs only its own.
                _live = {"long": 0.0, "short": 0.0}
                if size > 0 and exch_side in _live:
                    _live[exch_side] = size
                for _side, _j in (("long", _j_long), ("short", _j_short)):
                    if _j <= 0:
                        continue
                    _backed = _live[_side]
                    if _j <= _backed * (1.0 + _BYBIT_QTY_DIVERGENCE_FRAC):
                        continue
                    summary["journal_qty_divergent"] += 1
                    _pairs_q = pairs_qty_by_key.get((*cache_key, _side), 0.0)
                    if _pairs_q >= _j - 1e-9:
                        # ENTIRELY pairs-sleeve. The netting reconciler refuses
                        # these by design, so the generic message below would
                        # name a remediation that will never come — a permanent
                        # ERROR no operator can action, which is how alarm
                        # fatigue starts. Say who owns it instead, and keep it
                        # at WARNING: the divergence is real, but "nobody is
                        # coming" is a different fact from "a reconciler will
                        # fix this next tick".
                        summary["journal_qty_divergent_pairs"] += 1
                        logger.warning(
                            "_check_broker_naked_bybit_positions: PAIRS-SLEEVE "
                            "QTY DIVERGENCE %s/%s %s — journal %s vs exchange "
                            "%s (excess %s). These rows belong to "
                            "pairs_executor, which owns its own state, so the "
                            "netting reconciler SKIPS them (skipped_pairs) and "
                            "no attribution pass will ever reduce them. Not "
                            "actionable here by design — fixing it means "
                            "teaching pairs_executor to reconcile its own legs "
                            "(BL-20260808-PAIRS-DIVERGENCE-UNOWNED).",
                            account_id, symbol, _side, _j, _backed, _j - _backed,
                        )
                        continue
                    logger.error(
                        "_check_broker_naked_bybit_positions: JOURNAL/BROKER QTY "
                        "DIVERGENCE %s/%s %s — open journal rows sum to %s but the "
                        "exchange backs %s on that side (position: size=%s "
                        "side=%s; excess %s%s). At least one open row is a phantom; "
                        "analytics and risk sizing both read the journal.",
                        account_id, symbol, _side, _j, _backed,
                        size, exch_side or "flat", _j - _backed,
                        f"; {_pairs_q} of the journal qty is pairs-sleeve-owned "
                        f"and unattributable" if _pairs_q > 0 else "",
                    )
                if size > 0 and not exch_side:
                    # Side unreadable ⇒ we cannot say which journal rows the
                    # position backs. Say so rather than grade it against a
                    # guess (the coverage-ungradeable posture, below).
                    logger.warning(
                        "_check_broker_naked_bybit_positions: %s/%s exchange "
                        "position side unreadable (size=%s) — journal/broker qty "
                        "divergence not graded this tick.",
                        account_id, symbol, size,
                    )

            if size <= 0:
                continue  # flat — nothing to protect (divergence graded above)
            eps = size * _BYBIT_COVERAGE_EPS_FRAC

            # ---- leg OVER-accumulation (once per symbol, no action) ----------
            # Found live by the bybit-bracket-audit on 2026-07-30 and invisible
            # to every existing check. DETECT-ONLY here: remediation is a
            # separate, reviewed change (cancel-stale-tpsl-legs) — the point is
            # that it stops being silent.
            #
            # NOTE the guard set is `overcover_checked`, NOT `anomaly_checked`.
            # The divergence check above already added cache_key to the latter,
            # so sharing one set would make this block unreachable and silently
            # retire a live detector — the same never-looked failure the
            # divergence hoist exists to fix, one level over.
            if cache_key not in overcover_checked:
                overcover_checked.add(cache_key)
                # Resting SL legs summing to well over the position size means
                # legs have piled up (the BL-20260721 20-leg-cap failure mode).
                # Live example: bybit_1 XRPUSDT, position 32557.2, legs
                # 144789.3 (444.7%) — if the first leg trips it OVER-closes,
                # and the rest are stranded.
                if (
                    state["source"] == "partial_sl_legs"
                    and covered > size * _BYBIT_OVERCOVER_FACTOR
                ):
                    summary["over_covered"] += 1
                    # ⚠️ The TRIP is side-blind (see below); the DESCRIPTION is
                    # not. Before 2026-09-02 this line said "resting SL legs
                    # total X" over a sum that mixes both hedge books, so a
                    # position covered EXACTLY 1.00x by its own legs was
                    # reported as 2656% over-protected.
                    _split = state.get("leg_side_split")
                    _which, _split_fields = _bybit_over_cover_condition(
                        size=size, covered=covered, leg_side_split=_split,
                    )
                    if _split_fields.get("other_book_legs"):
                        summary["over_cover_other_book"] = (
                            summary.get("over_cover_other_book", 0) + 1)
                    if _split_fields.get("leg_side_split_state") != "computed":
                        summary["over_cover_split_ungraded"] = (
                            summary.get("over_cover_split_ungraded", 0) + 1)
                    logger.error(
                        "_check_broker_naked_bybit_positions: OVER-COVER TRIP "
                        "%s/%s — position size=%s side=%s; side-blind SL total "
                        "%s (%.0f%%) across %d leg(s) is what tripped the "
                        "check. %s Remedy: cancel-stale-tpsl-legs, DRY-RUN "
                        "first and check its plan against the journal.",
                        account_id, symbol, size, exch_side or "unreadable",
                        covered,
                        (100.0 * covered / size) if size else 0.0,
                        len(state["sl_leg_ids"]), _which,
                    )
                    # ...and PAGE the operator. The logger.error above reaches
                    # the systemd journal and nothing else, so this condition
                    # was detected and invisible on every surface a human reads
                    # (measured: 0 Bybit rows in a 401-row operator ERROR+ feed
                    # while the IB equivalent fired 3 times in the same feed).
                    if _emit_bybit_over_cover_alert(
                        account_id=account_id,
                        symbol=symbol,
                        size=size,
                        covered=covered,
                        leg_count=len(state["sl_leg_ids"]),
                        protective_leg_count=state.get("protective_leg_count"),
                        leg_side_split=_split,
                    ):
                        summary["over_cover_alerted"] = (
                            summary.get("over_cover_alerted", 0) + 1)
            # An SL leg whose qty we could not parse makes coverage ungradeable.
            # Do NOT re-arm on a guess (a Full-mode re-arm would stamp ONE
            # trade's levels over the whole netted position); surface it instead.
            if state["unknown_qty_sl_legs"] and state["source"] == "partial_sl_legs":
                summary["unconfirmed"] += 1
                logger.warning(
                    "_check_broker_naked_bybit_positions: %s/%s has %d SL leg(s) "
                    "with unparseable qty — coverage ungradeable, skipping re-arm "
                    "(size=%s covered>=%s). Run the bybit-bracket-audit action.",
                    account_id, symbol, state["unknown_qty_sl_legs"], size, covered,
                )
                protection_cache[cache_key] = None  # don't re-grade this tick
                continue
            # ---- COVERAGE OF THE GRADED BOOK ---------------------------------
            # ⚠️ `covered` above is SIDE-BLIND and stays that way: it feeds the
            # over-cover TRIP (a union of two conditions — narrowing it would
            # make the other-book case go silent) and the page. It must NOT
            # decide re-arm. A protective leg is reduce-only, so it acts on the
            # book it can SHRINK; since HEDGE mode was armed on bybit_1/bybit_2
            # (2026-08-30, BYBIT_HEDGE_MODE_SYMBOLS) a symbol can carry legs for
            # TWO books in that one sum, and an OTHER-book leg can push the
            # total past `size` on a position whose OWN stop is gone — so the
            # test below would skip a genuinely naked position as fully covered.
            #
            # CONSTRUCTED from the live 2026-09-02T03:30:33Z read (bybit_1
            # BTCUSDT: Buy 0.018 idx=1, own Sell 0.018 SL, plus Buy 0.46 SL on
            # the other book): lose the Sell 0.018 and `covered` still reads
            # 0.46 >= 0.018. ⚠️ n = 1, CONSTRUCTED — no live instance of the
            # masking has been observed.
            if state["source"] == "partial_sl_legs":
                graded_covered, _cov_reason = _bybit_leg_sides.graded_book_coverage(
                    state.get("leg_side_split"))
            else:
                # `full_position_stop` genuinely covers the WHOLE net position
                # and returns before the legs are read at all, so there is no
                # split to grade and `covered_qty == size` is the measurement.
                graded_covered, _cov_reason = covered, _bybit_leg_sides.COVERAGE_GRADED
            if graded_covered is None:
                # WE COULD NOT LOOK. Refuse in BOTH directions — do not re-arm
                # (a Full-mode re-arm is a live order that stamps ONE trade's
                # levels over the whole netted position, and under hedge mode it
                # would target a book we just failed to identify), and do not
                # bank the side-blind sum as coverage either. This is the same
                # posture the `unknown_qty_sl_legs` guard above already takes,
                # and it is LOUD: a WARNING plus its own counter, so the refusal
                # is a reportable condition rather than a silent skip.
                summary["coverage_side_ungradeable"] += 1
                logger.warning(
                    "_check_broker_naked_bybit_positions: %s/%s coverage of the "
                    "GRADED book is ungradeable (%s) — skipping re-arm "
                    "(size=%s, side-blind SL total=%s, position side=%s). The "
                    "side-blind total is NOT coverage of this book and is not "
                    "used to skip. Run the bybit-bracket-audit action.",
                    account_id, symbol, _cov_reason, size, covered,
                    exch_side or "unreadable",
                )
                protection_cache[cache_key] = None  # don't re-grade this tick
                continue
            if graded_covered + eps >= size:
                continue  # fully covered ON THE BOOK WE GRADED
            # ---- NOT fully covered -------------------------------------------
            # covered == 0 → fully naked (the pre-2026-07-30 case).
            # 0 < covered < size → PARTIALLY naked: the netted position carries
            # SOME per-trade legs but not enough qty to cover all of it. This is
            # the case the old any()-boolean silently skipped.
            partial = graded_covered > 0
            summary["broker_naked"] += 1
            if partial:
                summary["partially_naked"] += 1
                logger.error(
                    "_check_broker_naked_bybit_positions: PARTIALLY NAKED "
                    "%s/%s — position size=%s but resting SL legs on THIS book "
                    "cover only %s (uncovered=%s; side-blind SL total across "
                    "both books is %s). Per-trade qty-scoped legs have been lost "
                    "(20-leg cap rejection, or cancelled with a sibling close).",
                    account_id, symbol, size, graded_covered,
                    size - graded_covered, covered,
                )
            summary["broker_naked_qty_uncovered"] = int(
                summary.get("broker_naked_qty_uncovered", 0)
            ) + 1
            sl = row["stop_loss"]
            tp = row["take_profit_1"]
            a_sl = sl if (sl not in (None, 0) and sl > 0) else None
            a_tp = tp if (tp not in (None, 0) and tp > 0) else None
            if a_sl is None or a_tp is None:
                r_sl, r_tp = _resolve_protective_levels(
                    db, symbol, str(row["direction"] or "")
                )
                a_sl = a_sl if a_sl is not None else r_sl
                a_tp = a_tp if a_tp is not None else r_tp
            if a_sl is None or a_tp is None:
                logger.warning(
                    "_check_broker_naked_bybit_positions: trade_id=%s %s "
                    "broker-naked but no SL/TP resolvable — leaving for alert",
                    row["id"], symbol,
                )
                continue
            # A PARTIAL gap is topped up with a qty-scoped Partial SL leg for
            # exactly the uncovered qty, so the trades that still hold a good
            # leg keep the geometry they chose. Only if that is refused do we
            # fall back to the whole-position Full-mode re-arm (which protects
            # everything but stamps one trade's levels over the netted position).
            topped_up = False
            if partial:
                # The hole is measured against the GRADED book. Sizing the
                # top-up off the side-blind sum would place a leg for the wrong
                # quantity — or, once the other book's legs exceed `size`, for a
                # negative one.
                uncovered = size - graded_covered
                topped_up = _bybit_top_up_partial_sl(
                    acc, symbol, row, uncovered, a_sl,
                )
                if topped_up:
                    summary["topped_up"] += 1
                    # A qty-scoped top-up mutates this trade's own protection
                    # too — narrower than a Full-mode re-arm, but still a
                    # repair, and stamped as its own kind so a reader can tell
                    # the two apart. Like the naked re-arm it adds no read-back,
                    # hence the default `unverified`.
                    _stamp_repair(db, row, "partial_topup")
                    protection_cache[cache_key] = _bybit_mark_fully_covered(
                        state, size)
                    logger.warning(
                        "_check_broker_naked_bybit_positions: topped up %s/%s "
                        "with a qty-scoped Partial SL leg for the uncovered %s "
                        "@ sl=%s (trade_id=%s)",
                        account_id, symbol, uncovered, a_sl, row["id"],
                    )
            if not topped_up and _attempt_naked_autoprotect(
                row, a_sl, a_tp, db=db):
                summary["rearmed"] += 1
                protection_cache[cache_key] = _bybit_mark_fully_covered(state, size)
                logger.info(
                    "_check_broker_naked_bybit_positions: re-armed Full-mode "
                    "position bracket (sl=%s tp=%s) on broker-naked trade_id=%s "
                    "account=%s symbol=%s (partial=%s)",
                    a_sl, a_tp, row["id"], account_id, symbol, partial,
                )
        except Exception as exc:  # noqa: BLE001
            summary["errors"] += 1
            logger.warning(
                "_check_broker_naked_bybit_positions: failed for trade_id=%s: %s",
                row["id"], exc,
            )
    return summary


def _check_naked_positions(db) -> Dict[str, int]:
    """Scan open live trades for missing or non-positive SL/TP values.

    Logs WARNING and enqueues a Telegram alert for each naked trade.
    Idempotent: the alert is stamped into ``trades.notes`` so subsequent
    ticks don't re-fire the same ping. Never raises.

    Returns ``{"checked", "naked", "alerted", "errors"}`` counts.
    """
    summary: Dict[str, int] = {
        "checked": 0, "naked": 0, "alerted": 0, "protected": 0, "errors": 0,
    }
    try:
        conn = db.connect()
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT id, account_id, symbol, direction, position_size, "
                "stop_loss, take_profit_1, created_at, notes "
                "FROM trades "
                "WHERE status='open' AND COALESCE(is_backtest,0)=0 "
                "AND ("
                "  stop_loss IS NULL OR stop_loss <= 0 "
                "  OR take_profit_1 IS NULL OR take_profit_1 <= 0"
                ")"
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_check_naked_positions: DB read failed: %s", exc)
        summary["errors"] += 1
        return summary

    summary["checked"] = len(rows)
    if not rows:
        return summary

    now = datetime.now(timezone.utc)
    for row in rows:
        trade_id = row["id"]
        created = _parse_created_at(row["created_at"])
        if (
            created is not None
            and (now - created).total_seconds() < _NAKED_POSITION_GRACE_SECONDS
        ):
            continue  # still within grace window

        summary["naked"] += 1
        notes = _decode_notes(row["notes"])
        already_attached = bool(notes.get("naked_sltp_attached_at"))
        already_alerted = bool(notes.get("naked_sltp_alerted_at"))
        if already_attached:
            continue  # already auto-protected; nothing to do
        # A position alerted-but-never-attached still gets an attach attempt
        # every tick — "alerted" is not "protected". Re-arming a naked position
        # is baseline correctness, not an opt-in: an open trade with no stop is
        # an unacceptable state the system must always fix.

        sl = row["stop_loss"]
        tp = row["take_profit_1"]
        account = str(row["account_id"] or "unknown")
        symbol = str(row["symbol"] or "?")
        side = str(row["direction"] or "?")

        # Auto-protect (unconditional, IB-only): attach a reverse-side GTC SL/TP
        # bracket before falling back to the alert. A naked position is an
        # unacceptable state, not an opt-in feature — the system always re-arms
        # it. Missing levels resolve from the originating order package (an
        # adopted orphan carries NULL sl/tp); non-IB accounts no-op inside
        # _attempt_naked_autoprotect and fall through to the alert below.
        a_sl = sl if (sl not in (None, 0) and sl > 0) else None
        a_tp = tp if (tp not in (None, 0) and tp > 0) else None
        if a_sl is None or a_tp is None:
            r_sl, r_tp = _resolve_protective_levels(db, symbol, side)
            a_sl = a_sl if a_sl is not None else r_sl
            a_tp = a_tp if a_tp is not None else r_tp
        if a_sl is not None and a_tp is not None and _attempt_naked_autoprotect(
            row, a_sl, a_tp, db=db
        ):
            summary["protected"] += 1
            attached_notes = dict(notes)
            attached_notes["naked_sltp_attached_at"] = now.isoformat()
            attached_notes["naked_sltp_attached_levels"] = {
                "sl": a_sl, "tp": a_tp,
            }
            try:
                db.update_trade(trade_id, {
                    "stop_loss": a_sl,
                    "take_profit_1": a_tp,
                    "notes": json.dumps(attached_notes),
                })
            except Exception as upd_exc:  # noqa: BLE001
                logger.warning(
                    "_check_naked_positions: row update after attach "
                    "failed for trade_id=%s: %s", trade_id, upd_exc,
                )
            logger.info(
                "_check_naked_positions: auto-attached GTC SL/TP "
                "(sl=%s tp=%s) to naked trade_id=%s account=%s symbol=%s",
                a_sl, a_tp, trade_id, account, symbol,
            )
            continue  # protected; no naked alert needed

        # Auto-protect didn't attach this tick (off, levels unresolved, or the
        # IB place failed). If we already alerted on a prior tick, don't re-alert
        # — just leave it for the next attach attempt.
        if already_alerted:
            continue

        logger.warning(
            "_check_naked_positions: open trade id=%s account=%s symbol=%s "
            "side=%s sl=%r tp=%r — naked position, SL/TP must be set manually",
            trade_id, account, symbol, side, sl, tp,
        )
        try:
            from src.runtime.execution_diagnostics import enqueue_naked_position_alert
            enqueue_naked_position_alert(
                trade_id=trade_id,
                account=account,
                symbol=symbol,
                side=side,
                sl=sl,
                tp=tp,
            )
            summary["alerted"] += 1
            updated_notes = dict(notes)
            updated_notes["naked_sltp_alerted_at"] = now.isoformat()
            try:
                db.update_trade(trade_id, {"notes": json.dumps(updated_notes)})
            except Exception as stamp_exc:  # noqa: BLE001
                logger.warning(
                    "_check_naked_positions: notes stamp failed for "
                    "trade_id=%s: %s",
                    trade_id, stamp_exc,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_check_naked_positions: alert enqueue failed for "
                "trade_id=%s: %s",
                trade_id, exc,
            )
            summary["errors"] += 1
    return summary


def _sweep_pending_pnl_from_bybit(db) -> Dict[str, int]:
    """Fill ``pnl`` / ``exit_price`` / the broker-PnL note for any DB-closed
    trade that hasn't yet been reconciled against its broker's authoritative
    closed-pnl record.

    .. note::
       **The name is historical: this is no longer Bybit-only.** It dispatches
       through :func:`~src.units.accounts.clients.account_closed_pnl_for_trade`,
       which serves every exchange in ``BROKER_PNL_READER_EXCHANGES`` — Bybit's
       ``/v5/position/closed-pnl`` and, since 2026-07-30, IBKR executions read
       from the exchange-fills store. The provenance stamp and the notes key are
       both taken from the RECORD (``_broker_pnl_source`` /
       ``_broker_pnl_note_key``), never hardcoded, so an IBKR fill is never
       labelled Bybit truth. Kept un-renamed deliberately: the symbol is
       referenced across the monitor, the reconciler docs and the runbooks, and
       a rename is churn with no behavioural payoff — but a stale docstring is
       exactly the "field beats comment" drift the repo's rules call out, hence
       this note.

    Sister sweep to :func:`_reconcile_open_trades`. Where that one
    detects DB-open / exchange-flat orphans, this one detects DB-
    closed / pnl-still-pending rows. The combination guarantees
    every live trade's final ``pnl`` is Bybit-truth, never a fee-
    blind local computation.

    Adopted 2026-05-18 as part of the SSOT PnL refactor (operator
    directive: "Bybit is the only source of trade data; the system
    doesn't need its own calculator"). The historical fee-blind
    ``_compute_close_pnl`` was deleted in the same change; close
    paths now leave ``pnl`` NULL and this sweep fills it on the next
    monitor tick once Bybit's closed-pnl record is available (usually
    30-60 s after the close fill).

    Runs unconditionally every monitor tick (the MONITOR_RECONCILE_ENABLED
    gate was removed 2026-06-15, BL-20260615-MGCNAKED — self-heal is baseline
    correctness). Best-effort — never raises.

    Returns:
        dict: ``{"scanned", "filled", "still_pending", "errors"}``
        counts for the tick. ``still_pending`` is the expected
        steady state for a freshly-closed row whose Bybit record
        hasn't propagated yet — it'll flip to ``filled`` on the
        next tick.
    """
    summary: Dict[str, int] = {
        "scanned": 0, "filled": 0, "still_pending": 0, "errors": 0,
        # Declared here rather than created on first use, so a run that
        # re-classified NOTHING reports 0 instead of omitting the key —
        # "we looked and none qualified" must not read as "we did not look".
        "reclassified": 0,
    }

    # Scope: closed, non-backtest, pnl IS NULL, opened within
    # Bybit's 7-day closed-pnl retention window. Cap at 50 to
    # bound per-tick API load — the sweep runs every tick so a
    # backlog drains in a couple of minutes.
    try:
        conn = db.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, symbol, direction, position_size, "
                "       entry_price, account_id, created_at, notes, "
                "       setup_type, exit_reason "
                "  FROM trades "
                " WHERE status = 'closed' "
                "   AND COALESCE(is_backtest, 0) = 0 "
                "   AND pnl IS NULL "
                "   AND datetime(created_at) >= "
                "       datetime('now', '-7 days') "
                " ORDER BY datetime(created_at) DESC "
                " LIMIT 50"
            )
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_sweep_pending_pnl_from_bybit: scan query failed: %s", exc,
        )
        return summary

    if not rows:
        return summary
    summary["scanned"] = len(rows)

    try:
        cfgs = _load_account_cfgs_for_reconcile()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_sweep_pending_pnl_from_bybit: account cfg load failed: %s",
            exc,
        )
        summary["errors"] = len(rows)
        return summary
    if not cfgs:
        # No accounts configured → can't ask Bybit. Rows stay pending.
        summary["still_pending"] = len(rows)
        return summary

    try:
        from src.units.accounts.clients import (
            account_closed_pnl_for_trade,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_sweep_pending_pnl_from_bybit: clients import failed: %s",
            exc,
        )
        summary["errors"] = len(rows)
        return summary

    for row in rows:
        try:
            aid = row.get("account_id")
            cfg = cfgs.get(aid) if aid else None
            if cfg is None:
                # Row was booked under an account no longer in YAML
                # (typical for retired accounts). Skip silently — the
                # backfill script can target these explicitly.
                summary["still_pending"] += 1
                continue

            opened_at_ms = _isoformat_to_ms(row.get("created_at"))
            if opened_at_ms is None:
                summary["still_pending"] += 1
                continue

            try:
                rec = account_closed_pnl_for_trade(
                    cfg,
                    symbol=str(row.get("symbol") or ""),
                    direction=str(row.get("direction") or ""),
                    opened_at_ms=opened_at_ms,
                    qty=_safe_float(row.get("position_size")),
                    entry_price=_safe_float(row.get("entry_price")),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_sweep_pending_pnl_from_bybit: closed-pnl lookup "
                    "raised for trade_id=%s: %s",
                    row.get("id"), exc,
                )
                summary["errors"] += 1
                continue

            if rec is None or not rec.get("avg_exit_price"):
                # Bybit hasn't booked the record yet. Try again next
                # tick. This is the steady-state path for trades that
                # just closed seconds ago.
                summary["still_pending"] += 1
                continue

            # Got Bybit truth — write it.
            avg_exit_price = float(rec["avg_exit_price"])
            closed_pnl = rec.get("closed_pnl")
            notes = _decode_notes(row.get("notes"))
            notes["exit_price_source"] = _broker_pnl_source(rec)
            _reclassified: Optional[str] = None

            # ── Re-classify the exit now that a price EXISTS ──────────────
            # BL-20260822-EXIT-REASON-FROZEN-WHEN-PRICE-ARRIVES-LATE.
            #
            # `_close_trade_from_order_status`'s no-record fallback hard-codes
            # ``exit_reason='reconciler_filled'`` and leaves ``exit_price`` NULL
            # — CORRECTLY, because at that moment there is no price to classify
            # against. This sweep is the moment the price arrives, and until now
            # it wrote the price and left the LABEL frozen at the one instant the
            # answer could not be known. Measured 2026-08-22 over the 395
            # gradeable ``reconciler_filled`` rows: on the 155 carrying a
            # BROKER-TRUTH price, **91 (58.7%)** had actually reached a declared
            # bracket level, and **181 of 181** mislabelled rows carried no
            # ``exit_reason_source`` key at all — a 100% signature that none had
            # ever reached the classifier.
            #
            # THREE GUARDS, each load-bearing:
            #   1. ``is_reduce_leg`` is derived exactly as at the other call site.
            #      A reduce's bracket can be INVERTED relative to the order
            #      direction, so classifying one would mislabel it as sl/tp —
            #      the precise failure `_classify_broker_exit`'s own exclusion
            #      exists to prevent. The sweep's SELECT did not read
            #      ``setup_type`` before this change; it does now.
            #   2. Only a row still carrying the GENERIC reason is relabelled.
            #      This sweep selects on ``pnl IS NULL``, which can include rows
            #      a different path closed with a real reason (``pairs_*``,
            #      ``sl_cross``, …). Overwriting one of those would destroy a
            #      better record than the one being written.
            #   3. Failure is swallowed HERE and never reaches the price write.
            #      The pnl/exit_price update is the load-bearing half; a
            #      bookkeeping label must never be able to lose it. Same guard
            #      shape as `_record_trade_cost_estimate` and the pairs cascade.
            try:
                _setup_type = str(row.get("setup_type") or "").strip().lower()
                _is_reduce = (
                    _setup_type == "intent_reduce"
                    or bool(notes.get("intent_reduce"))
                )
                _current_reason = str(row.get("exit_reason") or "").strip()
                if _current_reason in ("", "reconciler_filled"):
                    _resolved = _classify_broker_exit(
                        db, row, avg_exit_price, is_reduce_leg=_is_reduce,
                    )
                    # Stamp the marker ONLY when the classifier actually ran, so
                    # its ABSENCE keeps meaning "never reached the classifier" —
                    # that is what made the 181/181 signature readable at all.
                    notes["exit_reason_source"] = (
                        "price_vs_pkg_bracket" if _resolved else "unresolved"
                    )
                    if _resolved:
                        _reclassified = _resolved
            except Exception as exc:  # noqa: BLE001 — never lose the price write
                logger.warning(
                    "_sweep_pending_pnl_from_bybit: exit re-classification "
                    "failed for trade_id=%s (price/pnl still written): %s",
                    row.get("id"), exc,
                )
            if closed_pnl is not None:
                notes[_broker_pnl_note_key(rec)] = closed_pnl
            if rec.get("closed_at") and "closed_at" not in notes:
                # Normalise epoch-ms → ISO (BL-20260620-RECONCILER-CLOSEDAT-MS).
                notes["closed_at"] = (
                    normalize_closed_at_value(rec["closed_at"])
                    or str(rec["closed_at"])
                )

            updates: Dict[str, Any] = {
                "exit_price": avg_exit_price,
                "notes": dump_capped(notes, 500),
            }
            if _reclassified:
                updates["exit_reason"] = _reclassified
                summary["reclassified"] += 1
            if closed_pnl is not None:
                try:
                    updates["pnl"] = float(closed_pnl)
                    _entry = _safe_float(row.get("entry_price"))
                    _qty = _safe_float(row.get("position_size"))
                    if _entry and _qty and _entry * _qty > 0:
                        updates["pnl_percent"] = round(
                            float(closed_pnl)
                            / (_entry * _qty) * 100.0,
                            4,
                        )
                except (TypeError, ValueError):
                    pass

            try:
                db.update_trade(int(row["id"]), updates)
                summary["filled"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_sweep_pending_pnl_from_bybit: db update failed "
                    "for trade_id=%s: %s",
                    row.get("id"), exc,
                )
                summary["errors"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_sweep_pending_pnl_from_bybit: row %s raised: %s",
                row.get("id"), exc,
            )
            summary["errors"] += 1

    if summary["filled"] > 0 or summary["errors"] > 0:
        logger.info(
            "_sweep_pending_pnl_from_bybit: scanned=%d filled=%d "
            "still_pending=%d errors=%d",
            summary["scanned"], summary["filled"],
            summary["still_pending"], summary["errors"],
        )

    return summary


_BROKER_PNL_RECOVERY_MS = 7 * 24 * 60 * 60 * 1000  # Bybit closed-pnl retention

# A broker-reader (Bybit) row defers to its broker closed-pnl sweep for this
# long before the local-compute fallback rescues it. Set to the INV-2
# db-integrity grace (6h, ``check_db_integrity.DEFAULT_PNL_GRACE_HOURS``) so a
# row the broker reader can never fill — no closed-pnl record exists, e.g. a
# same-instant net-flat that never produced a realised record (BL-20260623-001,
# real-money #2783) — converges to local_compute before it would otherwise sit
# ``closed``/``pnl NULL`` for the full 7-day retention window and trip INV-2.
# Broker truth still wins WITHIN the grace (``_sweep_pending_pnl_from_bybit``
# owns the row there); this only catches rows the broker sweep abandons. Keep
# coupled to ``check_db_integrity.DEFAULT_PNL_GRACE_HOURS``.
_LOCAL_PNL_BROKER_DEFER_MS = 6 * 60 * 60 * 1000  # 6h — match INV-2 grace


def _exit_anchor_fetches_per_tick() -> int:
    """How many close-time candle fetches `_sweep_local_pnl_for_unpriced` may
    make per monitor tick (``EXIT_ANCHOR_FETCHES_PER_TICK``, default 3).

    A tuning knob, NOT an enable gate — the anchoring itself is unconditional
    (Prime Directive). `0` pauses the network path entirely without a redeploy,
    which leaves rows deferred rather than fabricated: the safe direction.
    """
    try:
        return max(0, int(os.environ.get("EXIT_ANCHOR_FETCHES_PER_TICK", "3")))
    except (TypeError, ValueError):
        return 3


def _sweep_local_pnl_for_unpriced(db) -> Dict[str, int]:
    """Local-compute the realised ``pnl`` the broker can't provide — the
    **universal fallback** half of the bot's PnL-resolution contract.

    Every account resolves realised PnL the same way: *prefer broker truth,
    fall back to local compute*. Whether an integration *can* provide broker
    truth is declared once, at the integration level
    (:data:`src.units.accounts.clients.BROKER_PNL_READER_EXCHANGES`); it is NOT
    a Bybit special-case in this sweep. The companion
    :func:`_sweep_pending_pnl_from_bybit` recovers fee-accurate ``closedPnl``
    for integrations that declare a broker reader; this sweep fills everything
    that reader can't (operator report 2026-06-16: IBKR MES/MGC/MHG on
    ``ib_paper`` + Alpaca/OANDA paper rows sat ``closed/orphaned, pnl NULL``
    forever and rendered ``$0.00``, because no broker reader is wired for them).

    PnL is computed from first principles — entry × exit × qty × direction ×
    ``contract_value_usd`` (the per-contract multiplier from
    ``config/instruments.yaml``). The exit price is the trade's recorded
    ``exit_price`` when present, else a **mark-to-market** last close from the
    bot's own candle feed (operator decision 2026-06-16). It also
    opportunistically **re-links** a row whose ``order_package_id`` is NULL back
    to its originating package, so the trade ↔ order-package ↔ result chain is
    complete.

    Source selection (declarative, not hardcoded):
      * Integration **without** a broker reader (`account_has_broker_pnl_reader`
        is False — IBKR/Alpaca/OANDA/…): local compute is the primary path; all
        eligible rows are filled here.
      * Integration **with** a broker reader (Bybit): the Bybit sweep owns
        fee-accurate recovery within the convergence grace
        (``_LOCAL_PNL_BROKER_DEFER_MS`` = the INV-2 db-integrity grace). This
        sweep only fills such a row once it is OLDER than that grace — i.e. the
        broker reader has had its window and still can't provide the number (no
        closed-pnl record exists; BL-20260623-001) — so we never pre-empt the
        fee-accurate number (preserves the 2026-05-18 SSOT-from-broker directive)
        while rescuing genuinely-abandoned rows from a permanent ``$0.00`` /
        ``closed``-with-NULL INV-2 violation rather than waiting the full 7-day
        broker retention window (``_BROKER_PNL_RECOVERY_MS``).

    Other guards: only ``position_size > 0`` rows (a ``rejected`` / never-filled
    row has no result and correctly stays NULL); 14-day window, ≤100 rows/tick.

    Runs every monitor tick alongside the Bybit sweep. Best-effort — never
    raises. Returns ``{"scanned", "filled", "relinked", "still_pending",
    "deferred_broker", "errors"}``.
    """
    summary: Dict[str, int] = {
        "scanned": 0, "filled": 0, "relinked": 0,
        "still_pending": 0, "deferred_broker": 0, "errors": 0,
        # Rows we ASKED about and could not anchor, so declared unmeasured
        # rather than priced from an unrelated mark. Counted, never silent.
        "declared_unmeasured": 0, "already_unmeasured": 0,
    }
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    try:
        conn = db.connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, symbol, direction, position_size, "
                "       entry_price, exit_price, account_id, created_at, "
                "       closed_at, notes, order_package_id, exit_reason "
                "  FROM trades "
                " WHERE status IN ('closed', 'orphaned') "
                "   AND COALESCE(is_backtest, 0) = 0 "
                "   AND pnl IS NULL "
                "   AND COALESCE(position_size, 0) > 0 "
                # BL-20260711: exclude intent_reduce reduce legs — their pnl is
                # DEFERRED (NULL) by design (apply_intent_reduce_partial_close),
                # so this universal mark-to-market fallback must NOT re-fabricate
                # it after _close_trade_from_order_status correctly leaves it
                # NULL. Keep this predicate in lockstep with the canonical
                # src.web.api._clean_trades.exclude_reduce_leg_predicate.
                "   AND COALESCE(setup_type, '') != 'intent_reduce' "
                "   AND COALESCE(notes, '') NOT LIKE '%\"intent_reduce\": true%' "
                "   AND datetime(created_at) >= datetime('now', '-14 days') "
                " ORDER BY datetime(created_at) DESC "
                " LIMIT 100"
            )
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("_sweep_local_pnl_for_unpriced: scan query failed: %s", exc)
        return summary

    if not rows:
        return summary
    summary["scanned"] = len(rows)

    try:
        cfgs = _load_account_cfgs_for_reconcile()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_sweep_local_pnl_for_unpriced: account cfg load failed: %s", exc,
        )
        cfgs = {}

    try:
        from src.runtime.local_pnl import (
            compute_pnl_percent,
            compute_realized_pnl,
            contract_value_usd_for,
        )
        from src.runtime.exit_anchor import ANCHOR_SOURCE, AnchorBudget, bar_close_at
        from src.runtime.fills_pnl import FILL_EXIT_SOURCE
        from src.runtime.provenance import UNMEASURED_MARKER
        from src.units.accounts.clients import account_has_broker_pnl_reader
    except Exception as exc:  # noqa: BLE001
        logger.warning("_sweep_local_pnl_for_unpriced: import failed: %s", exc)
        return summary

    # Per-tick NETWORK budget for the close-time anchor. Deliberately small:
    # this sweep runs on the LIVE trader's monitor tick, and an unbounded
    # per-row historical fetch here is the same shape as the 2026-06-09
    # cold-start wedge that pegged the 2-core box and froze the heartbeat.
    # Rows beyond the budget are DEFERRED to a later tick — never fabricated,
    # and never falsely declared unmeasured (we didn't actually look).
    anchor_budget = AnchorBudget(_exit_anchor_fetches_per_tick())

    for row in rows:
        try:
            aid = row.get("account_id")
            cfg = cfgs.get(aid) if aid else None
            # Options-expression accounts own their realised PnL via the
            # options-lifecycle reconciler (broker-confirmed expiry/assignment
            # cash, NOT this entry×exit×qty equity formula). Pricing an option
            # row here with the underlying's equity multiplier produces a bogus
            # number (the 2026-06-27 incident's phantom −$845). Skip them.
            try:
                from src.units.accounts.options_overlay import account_expresses_options
                if cfg is not None and account_expresses_options(cfg):
                    summary["deferred_options"] = summary.get("deferred_options", 0) + 1
                    continue
            except Exception:  # noqa: BLE001 — never let the guard crash the sweep
                pass
            # Broker-truth integrations: the Bybit closed-pnl sweep recovers
            # their fee-accurate PnL within the convergence grace. Defer to it
            # for rows still inside that grace; once the grace elapses, local-
            # compute rescues any row the broker reader still can't fill (no
            # closed-pnl record exists — BL-20260623-001) so it converges to a
            # truthful realised pnl instead of stranding ``closed``/NULL until
            # the 7-day retention window and tripping INV-2.
            if account_has_broker_pnl_reader(cfg):
                created_ms = _isoformat_to_ms(row.get("created_at"))
                if created_ms is None or (now_ms - created_ms) < _LOCAL_PNL_BROKER_DEFER_MS:
                    summary["deferred_broker"] += 1
                    continue

            symbol = str(row.get("symbol") or "")
            entry = _safe_float(row.get("entry_price"))
            qty = _safe_float(row.get("position_size"))

            # Exit price: recorded fill first, else the bar covering closed_at.
            #
            # 2026-07-30 (Tier-2, operator-approved). This previously fell back
            # to `last_mark_price(symbol)` — the market at SWEEP time, up to the
            # convergence grace AFTER the close — and booked pnl from it. For a
            # CONFIRMED CLOSE that is fabrication: a true value exists (the fill)
            # and the mark is not it. Matched-pair proof: trade 4180 (real)
            # -$4.00 vs mirror 4181 -$2,589.78 — same strategy, symbol, bracket
            # and minute, ~650x apart.
            #
            # The replacement is anchored to the close TIME (validated: median
            # 1.33 bps, p90 16.05, 46/48 within 50 bps) and is stamped
            # ESTIMATED, never MEASURED — a bar close says where the market was,
            # not where THIS order filled.
            exit_price = _safe_float(row.get("exit_price"))
            exit_source = "recorded_exit_price"
            if not exit_price or exit_price <= 0:
                anchored, anchor_status = bar_close_at(
                    symbol, row.get("closed_at"), budget=anchor_budget,
                )
                if anchor_status == "deferred":
                    # Budget spent or a transient read failure. We did NOT look,
                    # so we may not declare — retry on a later tick.
                    summary["still_pending"] += 1
                    continue
                if anchor_status == "no_anchor":
                    # We DID ask and the venue has no bar for that time (every
                    # IBKR root today — historical-candle coverage is 0%). The
                    # honest terminal state is to say so on the record, not to
                    # substitute an unrelated price. INV-2 accepts this marker
                    # and INV-2b counts it, so the row converges and stays
                    # visible instead of being fabricated or stranded.
                    notes = _decode_notes(row.get("notes"))
                    if notes.get("pnl_source") != UNMEASURED_MARKER:
                        notes["pnl_source"] = UNMEASURED_MARKER
                        notes["unmeasured_reason"] = "no_close_time_anchor"
                        try:
                            db.update_trade(int(row["id"]), {
                                "notes": dump_capped(notes, 500),
                            })
                            summary["declared_unmeasured"] = (
                                summary.get("declared_unmeasured", 0) + 1
                            )
                        except Exception:  # noqa: BLE001 — best-effort declaration
                            summary["errors"] += 1
                    else:
                        summary["already_unmeasured"] = (
                            summary.get("already_unmeasured", 0) + 1
                        )
                    continue
                exit_price = anchored
                exit_source = ANCHOR_SOURCE
            if not exit_price or exit_price <= 0:
                # Nothing usable this tick — retry later.
                summary["still_pending"] += 1
                continue

            cvu = contract_value_usd_for(symbol)
            pnl = compute_realized_pnl(
                entry_price=entry, exit_price=exit_price,
                qty=qty, direction=row.get("direction"),
                contract_value_usd=cvu,
            )
            if pnl is None:
                summary["still_pending"] += 1
                continue


            notes = _decode_notes(row.get("notes"))
            updates_exit_reason: Optional[str] = None
            # Net out the close-side fees when the exit came from real fills
            # that recorded them (stamped at close by the fills resolver). This
            # arithmetic was fee-BLIND, and that is a CORRECTNESS bug, not a
            # precision one: measured on the live store (diag #8114, 90d, Bybit
            # crypto) fees are 15.6% of gross realised in aggregate and 61.8%
            # on BTC — and the error is DIRECTIONAL, always flattering, so a
            # marginal loser books as a winner. Same bias that corrupts the ML
            # labels (BL-20260730-ML-LABELS-IGNORE-PNL-PROVENANCE).
            #
            # Gated on the row's OWN recorded exit provenance: an anchored or
            # mark-derived exit has no fee record, and estimating one would be
            # fabrication in the other direction. Equities/futures fills carry
            # fee=0.0, so this is a no-op there rather than a special case.
            _fees = _safe_float(notes.get("close_fees_usd")) or 0.0
            if _fees > 0 and notes.get("exit_price_source") == FILL_EXIT_SOURCE:
                pnl = round(pnl - _fees, 4)
                notes["pnl_net_of_close_fees"] = True
            # `local_compute` describes the ARITHMETIC, not the evidence — it is
            # deliberately unrecognised by the provenance vocabulary so
            # `classify_pnl` defers to `exit_price_source`, which is what
            # actually determines whether this number is trustworthy.
            notes["pnl_source"] = "local_compute"
            # ── NEVER OVERWRITE A MORE SPECIFIC STAMP (2026-08-24) ──────────
            # `BL-20260824-RECORDED-EXIT-PRICE-OUTNUMBERS-ALL-BROKER-TRUTH-COMBINED`.
            #
            # This assignment was unconditional, and that is how a projection
            # got laundered into a fill. The close path already labels its own
            # exit honestly — `exchange` when a fill lookup answered, `verdict`
            # when it did not (`_apply_update`) — and this sweep then stamped
            # `recorded_exit_price` over the top, which reads as the STRONGER
            # claim "a real fill the bot recorded".
            #
            # The distinction is whether THIS pass learned something about the
            # price's ORIGIN. The anchor branch did: it asked a venue for the
            # bar covering `closed_at`, so it stamps. The `recorded_exit_price`
            # branch did not — it only read a number already on the row — so it
            # must not claim to know where that number came from, and must
            # leave any existing stamp alone.
            if exit_source == ANCHOR_SOURCE or not notes.get("exit_price_source"):
                notes["exit_price_source"] = exit_source
            notes["contract_value_usd"] = cvu
            # A row that previously declared itself unmeasured and has now been
            # anchored must drop the marker, or INV-2b would count it forever.
            notes.pop("unmeasured_reason", None)

            # ── Re-derive the exit LABEL now that a price exists ────────────
            # BL-20260823-EXIT-LABEL-FROZEN-ON-THE-ANCHORED-PRICE-PATH.
            #
            # `_sweep_pending_pnl_from_bybit` learned this in #10151 (item 1.8);
            # THIS sweep — the sibling that prices every row Bybit broker truth
            # never covered — did not, so it wrote the price and left the label
            # frozen at `reconciler_filled`. Measured 2026-08-23 over the whole
            # journal: of 497 eligible rows (generic reason, reduce legs already
            # excluded by this query), 191 resolve to a real bracket level —
            # 156 of them off a MEASURED price. Fixing one sweep and not its
            # sibling is the "swept the instance, not the class" shape this repo
            # has named and re-paid for repeatedly.
            #
            # THE PRICE BASIS DECIDES WHETHER A LABEL IS DEFENSIBLE AT ALL.
            # `_classify_broker_exit` compares a price to the package bracket;
            # it is provenance-blind, and that is fine for a broker fill. It is
            # NOT fine for `local_markprice`, which is the market read at SWEEP
            # time — hours after the exit — so comparing it to the bracket
            # manufactures an sl/tp verdict out of later, unrelated price
            # action. That is the exact fabrication class `exit_anchor` was
            # built to stop, one level up in the label instead of the number.
            # So: classify on MEASURED or ESTIMATED, REFUSE on FABRICATED, and
            # say which happened rather than staying silent (105 of the 497 are
            # fabricated-price rows — a silent skip there is indistinguishable
            # from never having looked).
            #
            # THREE GUARDS, all load-bearing:
            #   1. Reduce legs are already excluded by this query's WHERE
            #      (setup_type + the notes LIKE), so no second derivation here —
            #      a duplicated predicate is one that can drift.
            #   2. Only a row still carrying the GENERIC reason is relabelled.
            #      This sweep selects on `pnl IS NULL`, which includes rows a
            #      different path closed with a real reason (`pairs_*`,
            #      `sl_cross`, `stale_stop`); overwriting one destroys a better
            #      record than the one being written.
            #   3. Failure is swallowed HERE and never reaches the price write.
            #      The pnl/exit_price update is the load-bearing half; a
            #      bookkeeping label must never be able to lose it.
            try:
                from src.runtime import provenance as _prov

                _cur_reason = str(row.get("exit_reason") or "").strip()
                if _cur_reason in ("", "reconciler_filled"):
                    _basis = _prov.classify(exit_source, "exit_price_source")
                    if _basis in (_prov.MEASURED, _prov.ESTIMATED):
                        _resolved = _classify_broker_exit(
                            db, row, exit_price, is_reduce_leg=False,
                        )
                        # Stamp ONLY when the classifier actually ran, so its
                        # ABSENCE keeps meaning "never reached the classifier" —
                        # the 100% signature that made this readable at all.
                        notes["exit_reason_source"] = (
                            _EXIT_LABEL_SOURCE_BY_BASIS[_basis]
                            if _resolved else "unresolved"
                        )
                        if _resolved:
                            updates_exit_reason = _resolved
                            notes["exit_reason_price_basis"] = _basis
                    else:
                        # We looked and DECLINED. Distinct from both "resolved"
                        # and "never ran".
                        notes["exit_reason_source"] = (
                            _prov.EXIT_LABEL_REFUSED_UNMEASURED
                        )
                        notes["exit_reason_price_basis"] = _basis
            except Exception as exc:  # noqa: BLE001 — never lose the price write
                logger.warning(
                    "_sweep_local_pnl_for_unpriced: exit re-classification "
                    "failed for trade_id=%s (price/pnl still written): %s",
                    row.get("id"), exc,
                )

            updates: Dict[str, Any] = {
                "pnl": pnl,
                "exit_price": float(exit_price),
                "notes": dump_capped(notes, 500),
            }
            if updates_exit_reason:
                updates["exit_reason"] = updates_exit_reason
            pct = compute_pnl_percent(
                pnl=pnl, entry_price=entry, qty=qty, contract_value_usd=cvu,
            )
            if pct is not None:
                updates["pnl_percent"] = pct

            # Opportunistic re-link: a row with no order_package_id (the
            # trade ↔ package gap the operator flagged) is matched back to its
            # originating package by symbol + direction + entry-within-tolerance.
            if not row.get("order_package_id") and entry:
                try:
                    recovered = _recover_orphan_order_package(
                        db=db, symbol=symbol,
                        direction=str(row.get("direction") or ""),
                        entry_price=float(entry),
                    )
                    if recovered and recovered.get("order_package_id"):
                        updates["order_package_id"] = str(
                            recovered["order_package_id"]
                        )
                        summary["relinked"] += 1
                except Exception:  # noqa: BLE001 — re-link is best-effort
                    pass

            db.update_trade(int(row["id"]), updates)
            summary["filled"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_sweep_local_pnl_for_unpriced: row %s raised: %s",
                row.get("id"), exc,
            )
            summary["errors"] += 1

    if (summary["filled"] or summary["relinked"] or summary["errors"]
            or summary["declared_unmeasured"]):
        logger.info(
            "_sweep_local_pnl_for_unpriced: scanned=%d filled=%d relinked=%d "
            "still_pending=%d deferred_broker=%d declared_unmeasured=%d "
            "already_unmeasured=%d errors=%d",
            summary["scanned"], summary["filled"], summary["relinked"],
            summary["still_pending"], summary["deferred_broker"],
            summary["declared_unmeasured"], summary["already_unmeasured"],
            summary["errors"],
        )
    return summary


def _options_executor_for(account_cfg: Dict[str, Any]):
    """Build an :class:`AlpacaOptionsExecutor` for an options-expressing account.

    Resolves the key pair from the account's ``api_key_env`` / ``api_secret_env``
    (defaulting to the shared ``ALPACA_API_KEY_ID`` / ``ALPACA_API_SECRET_KEY`` pair —
    the same contract as :func:`clients.alpaca_client_for`). Returns ``None`` when
    creds are unset (the reconciler then skips the account rather than orphaning a row).
    """
    try:
        key_env = str(account_cfg.get("api_key_env") or "ALPACA_API_KEY_ID")
        secret_env = str(account_cfg.get("api_secret_env") or "ALPACA_API_SECRET_KEY")
        api_key = os.environ.get(key_env, "")
        api_secret = os.environ.get(secret_env, "")
        if not api_key or not api_secret:
            return None
        from src.units.accounts.alpaca_options_exec import AlpacaOptionsExecutor
        return AlpacaOptionsExecutor(
            api_key=api_key, api_secret=api_secret,
            env=account_cfg.get("alpaca_env") or None,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_options_executor_for(%s): %s", account_cfg.get("account_id"), exc)
        return None


def _reconcile_options_expiry_and_assignment(db) -> Dict[str, int]:
    """Close options-expression journal rows the broker has concluded (Slice-4).

    A debit vertical opened by ``options_overlay.place_options_expression`` has no
    intraday close path — it rides to expiry. This sweep polls
    ``/v2/account/activities`` for expiration / assignment / exercise events and the
    open-option snapshot, and closes a row whose structure has **concluded**
    (``options_lifecycle.structure_concluded``: a lifecycle event was seen for its
    underlying AND that underlying no longer holds an open option position). Realized
    PnL is sourced from the activities' cash (``realized_pnl_from_activities``), NOT the
    equity entry×exit×qty formula — those rows are explicitly deferred in
    ``_sweep_local_pnl_for_unpriced``.

    Scoped to accounts where ``account_expresses_options`` is truthy, so it never
    touches an equity account's rows (the inverse of the 2026-06-27 shared-login
    incident). Requiring a broker-confirmed lifecycle event — not mere
    position-absence — keeps it from false-closing a just-opened position. Best-effort:
    never raises; a row whose underlying is ambiguous (two open rows share it, so the
    activity cash can't be split cleanly) is left for manual/next-tick resolution.
    Returns ``{accounts, checked, concluded, closed, ambiguous, skipped_no_creds, errors}``.
    """
    summary: Dict[str, int] = {
        "accounts": 0, "checked": 0, "concluded": 0, "closed": 0,
        "ambiguous": 0, "skipped_no_creds": 0, "errors": 0,
    }
    try:
        from src.config.accounts_loader import load_accounts_dict
        from src.units.accounts.options_overlay import account_expresses_options
        from src.units.accounts.options_lifecycle import (
            OPTION_LIFECYCLE_ACTIVITY_TYPES,
            realized_pnl_from_activities,
            structure_concluded,
            underlying_from_occ,
            underlyings_with_open_options,
        )
        raw = load_accounts_dict() or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_reconcile_options_expiry_and_assignment: setup failed: %s", exc)
        return summary

    opt_accounts: Dict[str, Dict[str, Any]] = {}
    for name, cfg in raw.items():
        if cfg.get("enabled") is False:
            continue
        merged = dict(cfg)
        merged["account_id"] = str(name)
        if account_expresses_options(merged):
            opt_accounts[str(name)] = merged
    if not opt_accounts:
        return summary
    summary["accounts"] = len(opt_accounts)

    try:
        lookback_days = float(os.environ.get("OPTIONS_LIFECYCLE_LOOKBACK_DAYS", "4") or 4)
    except (TypeError, ValueError):
        lookback_days = 4.0
    after_iso = (
        datetime.now(timezone.utc) - timedelta(days=max(0.0, lookback_days))
    ).date().isoformat()

    for aid, cfg in opt_accounts.items():
        try:
            open_rows = [
                r for r in (db.get_trades(filters={"account_id": aid, "status": "open"}) or [])
                if not r.get("is_backtest")
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_reconcile_options_expiry_and_assignment: open-rows read failed "
                "(account=%s): %s", aid, exc,
            )
            summary["errors"] += 1
            continue
        if not open_rows:
            continue

        executor = _options_executor_for(cfg)
        if executor is None:
            summary["skipped_no_creds"] += 1
            continue

        try:
            act_env = executor.account_activities(
                activity_types=list(OPTION_LIFECYCLE_ACTIVITY_TYPES), after=after_iso,
            )
            activities = act_env.get("result") if act_env.get("retCode") == 0 else None
            positions = executor.option_positions()  # None on read failure
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_reconcile_options_expiry_and_assignment: broker read failed "
                "(account=%s): %s", aid, exc,
            )
            summary["errors"] += 1
            continue
        # Need BOTH reads to conclude safely — a read failure must never close a row.
        if activities is None or positions is None:
            summary["errors"] += 1
            continue

        if not isinstance(activities, list):
            activities = []
        open_unders = underlyings_with_open_options(positions)
        evented_unders = {
            u for a in activities
            if str(a.get("activity_type") or "").strip().upper() in OPTION_LIFECYCLE_ACTIVITY_TYPES
            for u in [underlying_from_occ(a.get("symbol"))] if u
        }

        # Guard against ambiguous attribution: two open rows on the same underlying
        # would each claim the whole underlying's close cash. Count per underlying.
        under_counts: Dict[str, int] = {}
        for r in open_rows:
            u = str(r.get("symbol") or "").strip().upper()
            under_counts[u] = under_counts.get(u, 0) + 1

        for row in open_rows:
            summary["checked"] += 1
            underlying = str(row.get("symbol") or "").strip().upper()
            seen = underlying in evented_unders
            if not structure_concluded(
                underlying,
                open_option_underlyings=open_unders,
                lifecycle_event_seen=seen,
            ):
                continue
            summary["concluded"] += 1
            if under_counts.get(underlying, 0) > 1:
                summary["ambiguous"] += 1
                logger.warning(
                    "_reconcile_options_expiry_and_assignment: %s has %d open rows on "
                    "%s — ambiguous activity attribution, leaving for manual resolution.",
                    aid, under_counts[underlying], underlying,
                )
                continue

            net_debit = _safe_float(row.get("entry_price")) or 0.0
            contracts = int(_safe_float(row.get("position_size")) or 0)
            life = realized_pnl_from_activities(
                activities, underlying=underlying,
                net_debit=net_debit, contracts=contracts,
            )
            try:
                _close_options_row(db, row, life)
                summary["closed"] += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "_reconcile_options_expiry_and_assignment: close write failed "
                    "(account=%s trade=%s): %s", aid, row.get("id"), exc,
                )
                summary["errors"] += 1

    if summary["closed"] or summary["errors"] or summary["ambiguous"]:
        logger.info(
            "_reconcile_options_expiry_and_assignment: accounts=%d checked=%d "
            "concluded=%d closed=%d ambiguous=%d skipped_no_creds=%d errors=%d",
            summary["accounts"], summary["checked"], summary["concluded"],
            summary["closed"], summary["ambiguous"], summary["skipped_no_creds"],
            summary["errors"],
        )
    return summary


def _close_options_row(db, row: Dict[str, Any], life) -> None:
    """Close an options journal row + its order package with activity-sourced PnL.

    Writes ``status=closed`` / ``exit_reason='options_expiry_assignment'`` /
    ``closed_at`` / broker-sourced ``pnl`` to the trade, and closes the linked order
    package. The ``pnl_source`` + contributing activity ids land in the trade notes for
    auditability. Mirrors ``_full_close_trade_and_package`` but owns the PnL field
    (which that helper does not write).
    """
    tid = int(row["id"])
    pkg_id = row.get("order_package_id")
    closed_at_iso = datetime.now(timezone.utc).isoformat()

    if pkg_id:
        try:
            db.update_order_package(pkg_id, {
                "status": "closed",
                "close_reason": "options_expiry_assignment",
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_close_options_row: order_package close write failed for %s: %s",
                pkg_id, exc,
            )

    notes = _decode_notes(row.get("notes"))
    notes["pnl_source"] = getattr(life, "pnl_source", "alpaca_activity")
    notes["options_close_cash"] = getattr(life, "close_cash", None)
    notes["options_open_cost"] = getattr(life, "open_cost", None)
    notes["options_lifecycle_event_count"] = getattr(life, "event_count", 0)
    if getattr(life, "activity_ids", None):
        notes["options_activity_ids"] = life.activity_ids[:20]

    db.update_trade(tid, {
        "status": "closed",
        "exit_reason": "options_expiry_assignment",
        "closed_at": closed_at_iso,
        "pnl": float(getattr(life, "realized_pnl", 0.0) or 0.0),
        "notes": dump_capped(notes, 2000),
    })


def _phase(name: str):
    """Time ONE monitor phase into the per-tick cost record. NO-OP fallback.

    WHY THIS EXISTS. `/api/diag/tick_cost` measured the live trader at 104s mean
    / 125s max per tick against the operator's 60s exit-evaluation ask, split
    51.7% signal generation and **46.8% this function** (48.7s mean / 52.8s max,
    2026-08-10, 18 ticks). That split is what says decoupling the monitor onto
    its own loop would clear 60s -- by SEVEN SECONDS, because the monitor's own
    runtime becomes the cadence floor. Necessary and barely sufficient.

    With `order_monitor` measured as ONE wrap, nothing can say WHICH of the
    fourteen phases below spends the 48.7s, so nobody can tell whether that cost
    is reducible (batchable broker round-trips) or irreducible (real work) --
    and that is the difference between a decouple with headroom and one that
    silently stops meeting the ask the next time a position is added. Halving
    this function buys more margin than the decouple does and needs no new loop.

    MEASUREMENT ONLY. No phase is skipped, reordered, or budgeted -- a monitor
    that skips a position to stay inside a budget is a monitor that stopped
    monitoring, which is strictly worse than a slow one. Each phase keeps its own
    `try/except`; this wrapper records in `finally` and never swallows, so a
    phase that burns time and then throws still appears in the split rather than
    vanishing from it (the shape that would hide the expensive failure).

    Names are prefixed `monitor.` so they cannot collide with `src/main.py`'s
    top-level hooks, and `tick_cost` bounds the name table at `_MAX_HOOK_NAMES`
    with refusals COUNTED (`hook_names_refused`), so a truncated split can never
    read as a complete one.
    """
    try:
        from src.runtime.tick_cost import hook
        return hook(f"monitor.{name}")
    except Exception:  # noqa: BLE001
        # An instrumentation import error must never stop the live monitor.
        import contextlib
        return contextlib.nullcontext()


def run_exit_evaluation_tick(
    *,
    db_path: Optional[str] = None,
    ohlcv_fetcher: Optional[Callable[[str, Optional[str]], Any]] = None,
    strategies: Optional[List[str]] = None,
    strategy_cfg: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """THE EXIT HALF — evaluate every open package's monitor() and apply verdicts.

    Split out of `run_monitor_tick` on 2026-08-12 (Tier-2, operator-approved) so
    it can run on its OWN cadence. The measurement that forced the split
    (`docs/research/M20-exit-monitor-decouple-evidence-2026-08-10.md` § 4d, n=7,
    all 14 children populated and summing to 99.6% of the parent):

        strategy_monitor_loop   24.3 s mean / 28.2 s max   (49.3% of the monitor)
        every reconciler/sweep  24.8 s mean                (50.3%)

    The 60-second exit-evaluation ask is about THIS function. The reconcilers ask
    "has the journal caught up with the broker?", not "should this trade exit
    now?", and one is already gated to 300 s. Running the WHOLE monitor on its
    own loop clears the 60 s bar by 3.20 s (5.3%, on a max over seven ticks);
    running only this half clears it by 31.78 s (53%).

    Callers must NOT assume this and the reconciler half share a tick — that
    coupling is what `mark_active_close`/`is_active_close` replaced.
    """
    summaries: Dict[str, Dict[str, Any]] = {}
    # Reset the per-tick active-close set (BL-20260708-ALPACA-REARM-VS-CLOSE-
    # FIGHT). The strategy monitor loop below populates it as it attempts closes;
    # the broker-naked equity re-arm later in this tick reads it to avoid re-arming
    # a position we're actively flattening.
    # NO per-tick clear any more: the active-close map is TIME-bounded and
    # self-pruning (see `is_active_close`). Clearing here would re-introduce
    # exactly the coupling the decouple removes — the exit half's markers must
    # outlive its own pass to be visible to the reconciler half.
    try:
        db = _resolve_db(db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: DB unavailable: %s", exc)
        return summaries

    # Default to the LIVE strategies.yaml cfgs (M20 E3) so YAML-declared
    # exit levers reach monitor() for already-open packages; an explicit
    # strategy_cfg (tests) still wins.
    cfg_map = strategy_cfg if strategy_cfg is not None else _load_live_strategy_cfgs()

    with _phase("strategy_monitor_loop"):
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
                candle_count: Optional[int] = None
                tf_used = (normalised.get("meta") or {}).get("timeframe")
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
                            tf_used,
                            strategy_name,
                        )
                        if candles is not None and hasattr(candles, "__len__"):
                            candle_count = len(candles)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "order_monitor: ohlcv_fetcher failed for %s: %s",
                            normalised.get("symbol"), exc,
                        )
                        candles = None

                # Per-pkg dispatch trace. Operators investigating "the monitor
                # doesn't seem to be doing anything" need to see (a) that the
                # loop reached this package, (b) whether candles arrived, and
                # (c) the verdict shape. INFO so it shows in the systemd log
                # without DEBUG; bounded by (open_packages × strategies)
                # per tick which is small in practice.
                pkg_id_log = normalised.get("order_package_id")
                symbol_log = normalised.get("symbol")
                if candles is None:
                    logger.info(
                        "order_monitor: %s pkg=%s symbol=%s tf=%s candles=None "
                        "(monitor will short-circuit)",
                        strategy_name, pkg_id_log, symbol_log, tf_used,
                    )
                verdict, monitor_status = _call_strategy_monitor(
                    strategy_name, cfg, candles, normalised,
                )
                # Exit-coverage Phase 3: the dynamic exit is "blind" this tick when
                # candles were unavailable (couldn't evaluate) OR monitor() couldn't
                # run (module unresolvable / no monitor() / it raised). A healthy
                # ran-no-action tick (status="ok", verdict None) is NOT blind.
                _track_monitor_blindness(
                    pkg_id=pkg_id_log, strategy=strategy_name, symbol=symbol_log,
                    blind=(candles is None or monitor_status != "ok"),
                    reason=("candles_unavailable" if candles is None
                            else monitor_status),
                )
                if verdict is None:
                    summary.no_change_count += 1
                    if candles is not None:
                        logger.info(
                            "order_monitor: %s pkg=%s symbol=%s candles=%s "
                            "verdict=None (no action)",
                            strategy_name, pkg_id_log, symbol_log, candle_count,
                        )
                    continue

                logger.info(
                    "order_monitor: %s pkg=%s symbol=%s candles=%s verdict=%s",
                    strategy_name, pkg_id_log, symbol_log, candle_count, verdict,
                )
                _apply_update(db, normalised, verdict, summary)

            summaries[strategy_name] = summary.to_dict()
            # Per-strategy summary: log on every tick that had at least one
            # open package, even when nothing changed. Pre-this-PR the log
            # only fired when updated/closed > 0, which made a passive
            # monitor (no verdict-firing condition met) indistinguishable
            # from a broken / un-invoked monitor in the journal.
            if summary.open_count > 0:
                logger.info(
                    "order_monitor: %s — open=%d updated=%d closed=%d "
                    "no_change=%d errors=%d",
                    strategy_name, summary.open_count,
                    summary.updated_count, summary.closed_count,
                    summary.no_change_count, summary.error_count,
                )

    return summaries


def run_reconciliation_tick(
    *,
    db_path: Optional[str] = None,
    ohlcv_fetcher: Optional[Callable[[str, Optional[str]], Any]] = None,
    strategies: Optional[List[str]] = None,
    strategy_cfg: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """THE HYGIENE HALF — reconcilers, sweeps, watchdogs, broker-naked checks.

    The other 50.3% of the old `run_monitor_tick`. Latency here governs how fast
    the JOURNAL learns what the broker already did (and how fast a naked position
    is re-armed) — a real requirement, but a different one from exit-evaluation
    latency. Conflating the two is what made the monitor look indivisible.

    Reads the active-close markers the exit half wrote via `is_active_close`,
    which is time-bounded precisely so this function need not run in the same
    tick as the writer.
    """
    summaries: Dict[str, Dict[str, Any]] = {}
    try:
        db = _resolve_db(db_path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("order_monitor: DB unavailable: %s", exc)
        return summaries

    # BUG-042: write-back reconciler. Runs unconditionally every tick
    # (the MONITOR_RECONCILE_ENABLED gate was removed 2026-06-15,
    # BL-20260615-MGCNAKED — self-heal is baseline correctness).
    try:
        with _phase("reconcile_open_trades"):
            recon = _reconcile_open_trades(db)
        if recon.get("orphaned") or recon.get("errors"):
            summaries["__reconciler__"] = recon
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: reconciler raised: %s", exc)

    # 2026-05-11 incident PR: reverse reconciler. Walks the OTHER
    # direction — every exchange-side open position is checked for a
    # matching trades.status='open' row, and an orphan (Bybit-known,
    # journal-unknown) is either alerted, ADOPTed into the journal,
    # or market-closed depending on ORPHAN_POSITION_POLICY. Runs
    # unconditionally, after the forward reconciler so the journal
    # mutations from forward-orphan closures don't
    # produce spurious reverse-orphan adoptions on the same tick.
    try:
        with _phase("reconcile_orphan_exchange_positions"):
            reverse_recon = _reconcile_orphan_exchange_positions(db)
        if (
            reverse_recon.get("orphans_found")
            or reverse_recon.get("closed_disappeared")
            or reverse_recon.get("reattached_existing")
            or reverse_recon.get("resolved_closed")
            or reverse_recon.get("errors")
        ):
            summaries["__reverse_reconciler__"] = reverse_recon
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: reverse reconciler raised: %s", exc,
        )

    # 2026-05-18 SSOT PnL refactor: pending-pnl sweep. Picks up any
    # closed-but-pending row whose ``pnl`` is still NULL (because the
    # monitor-side close path no longer computes gross PnL locally)
    # and queries Bybit's closed-pnl endpoint for the authoritative
    # net number. Runs after both reconcilers so any newly-closed row
    # gets its first lookup attempt on the same tick it was closed.
    try:
        with _phase("sweep_pending_pnl_from_bybit"):
            pending_pnl = _sweep_pending_pnl_from_bybit(db)
        if (
            pending_pnl.get("filled")
            or pending_pnl.get("errors")
        ):
            summaries["__pending_pnl_sweep__"] = pending_pnl
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: pending-pnl sweep raised: %s", exc,
        )

    # 2026-06-16: local-PnL fallback sweep — the universal half of the
    # PnL-resolution contract (prefer broker truth, else local compute). The
    # Bybit sweep above recovers fee-accurate PnL for integrations that declare
    # a broker reader (clients.BROKER_PNL_READER_EXCHANGES); this fills every
    # row that reader can't (IBKR MES/MGC/MHG on ib_paper, Alpaca/OANDA paper)
    # from entry/exit/qty × contract multiplier (mark-to-market exit when no
    # broker fill exists) and re-links any row missing its order_package_id.
    # Broker-reader rows are deferred until past the broker recovery window so
    # fee-accurate truth is never pre-empted.
    try:
        with _phase("sweep_local_pnl_for_unpriced"):
            local_pnl = _sweep_local_pnl_for_unpriced(db)
        if local_pnl.get("filled") or local_pnl.get("errors"):
            summaries["__local_pnl_sweep__"] = local_pnl
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: local-pnl sweep raised: %s", exc,
        )

    # Slice-4 options-lifecycle reconciler: close options-expression rows the
    # broker has concluded (expiry / assignment / exercise), with PnL sourced from
    # /v2/account/activities. Scoped to options-expressing accounts; a no-op for any
    # deployment without one. Runs after the PnL sweeps (which now defer options rows
    # to it) so a row it closes this tick isn't first mis-priced by the equity sweep.
    try:
        with _phase("reconcile_options_expiry_and_assignment"):
            options_recon = _reconcile_options_expiry_and_assignment(db)
        if options_recon.get("closed") or options_recon.get("errors") or options_recon.get("ambiguous"):
            summaries["__options_lifecycle__"] = options_recon
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: options-lifecycle reconciler raised: %s", exc,
        )

    # BUG-049: sweep order_packages that are status='open' but have no
    # linked_trade_id (never executed). Runs unconditionally.
    try:
        with _phase("sweep_unlinked_packages"):
            _sweep_unlinked_packages(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: unlinked-pkg sweep raised: %s", exc)

    # Sweep order_packages that are status='open' AND linked to a trade
    # that has already reached a terminal status (orphaned,
    # exchange_rejected, closed, rejected, rejected_too_small). These
    # are the cascade-leak rows that keep the strategy-monocle gate
    # stuck and silently block every future signal for the strategy.
    # Runs unconditionally.
    try:
        with _phase("sweep_stuck_linked_packages"):
            _sweep_stuck_linked_packages(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: stuck-linked-pkg sweep raised: %s", exc)

    # Last line of defence: stuck-strategy watchdog. Catches packages
    # the orphan reconciler + linked-package sweep both missed (e.g.
    # the linked trade is genuinely status='open' but the strategy
    # somehow can't progress). Force-clears the package + cascades
    # the trade row + emits a high-priority operator alert.
    # Runs unconditionally.
    try:
        with _phase("watchdog_stuck_strategies"):
            watchdog_summary = _watchdog_stuck_strategies(db)
        if (
            watchdog_summary.get("alerted")
            or watchdog_summary.get("errors")
        ):
            summaries["__stuck_strategy_watchdog__"] = watchdog_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: stuck-strategy watchdog raised: %s", exc,
        )

    # PR 5 (2026-05-10): the S-055 borrow-orphan and S-060
    # position-orphan reconciler calls lived here. They only swept
    # spot-margin accounts (none exist post-PR-3) and were deleted
    # alongside their loop bodies.

    # Naked-position check: alert on any open live trade that has no valid
    # SL/TP. New orders are blocked at execute_pkg before reaching the
    # exchange; this sweep catches any pre-fix rows that slipped through.
    try:
        with _phase("check_naked_positions"):
            naked_summary = _check_naked_positions(db)
        if naked_summary.get("naked") or naked_summary.get("errors"):
            summaries["__naked_positions__"] = naked_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning("run_monitor_tick: naked-position check raised: %s", exc)

    # Broker-naked equity sweep (BL-20260629-ALPACA-NAKED-BRACKET): an Alpaca
    # position keeps its journal SL/TP while its day-TIF bracket legs are
    # cancelled at the RTH close, so it is broker-naked yet invisible to the
    # DB-driven check above. This re-arms a GTC OCO for any such position.
    try:
        with _phase("check_broker_naked_equity_positions"):
            broker_naked_summary = _check_broker_naked_equity_positions(db)
        if broker_naked_summary.get("broker_naked") or broker_naked_summary.get(
            "errors"
        ):
            summaries["__broker_naked_equity__"] = broker_naked_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: broker-naked equity sweep raised: %s", exc
        )

    # Broker-naked IB sweep (BL-20260709-IB-BROKER-PROTECTION-UNVERIFIED): an IB
    # futures/ETF position whose broker OCA bracket was never placed / got
    # cancelled / dropped during a Gateway breaker-flap keeps its journal SL/TP,
    # so it is broker-naked yet invisible to the DB-driven check above. This
    # asks the broker (account-wide reqAllOpenOrders) whether a resting
    # protective leg exists and re-arms a GTC OCA when none does. Cadence-gated
    # (IB_BROKER_NAKED_CHECK_SECONDS, default 300s) so the account-wide IB order
    # read is never a per-tick cost (BL-20260609 pacing class).
    try:
        with _phase("check_broker_naked_ib_positions"):
            ib_naked_summary = _check_broker_naked_ib_positions(db)
        if ib_naked_summary.get("broker_naked") or ib_naked_summary.get("errors"):
            summaries["__broker_naked_ib__"] = ib_naked_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: broker-naked IB sweep raised: %s", exc
        )

    # Broker-naked Bybit sweep (BL-20260729-BYBIT-NAKED-POSITION-BLINDSPOT): a
    # Bybit position keeps its journal SL/TP while its actual broker protection
    # is gone — under BYBIT_TPSL_MODE=partial the qty-scoped legs desync from the
    # netted one-way position (intent_reduce adds none; a close cancels its own
    # trade's legs; the 20-leg cap can block an amend), so a broker-naked Bybit
    # position is invisible to the DB-driven check above. This asks the broker
    # whether the net position carries a stop (Full position stopLoss OR a
    # resting Partial SL leg) and re-arms a Full-mode position bracket when none
    # does. The real-money bybit_2 XRPUSDT no-bracket incident (2026-07-29)
    # disproved the earlier "Bybit atomic at entry, can't go naked" assumption.
    try:
        with _phase("check_broker_naked_bybit_positions"):
            bybit_naked_summary = _check_broker_naked_bybit_positions(db)
        if bybit_naked_summary.get("broker_naked") or bybit_naked_summary.get(
            "errors"
        ):
            summaries["__broker_naked_bybit__"] = bybit_naked_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: broker-naked Bybit sweep raised: %s", exc
        )

    # Netting partial-close ATTRIBUTION (BL-20260801). Under one-way netting a
    # position-level exit shrinks ONE exchange position holding N journal rows,
    # and per-order close detection reduces at most one of them — so siblings
    # stay open forever (measured 451x inflation on bybit_1 SOLUSDT 2026-08-06).
    # Runs right after the sweep above so it reuses the same freshly-read
    # exchange truth. Default mode is `annotate`: it does all the work and
    # records the rows it WOULD reduce, without touching the money DB.
    try:
        with _phase("reconcile_netting_partial_closes"):
            netting_summary = _reconcile_netting_partial_closes(db)
        if (
            netting_summary.get("divergent")
            or netting_summary.get("closed")
            or netting_summary.get("errors")
        ):
            summaries["__netting_attribution__"] = netting_summary
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_monitor_tick: netting attribution reconciler raised: %s", exc
        )

    # S-067 follow-up #3: closed → exchange-flat invariant check.
    # BASELINE (2026-06-17) — runs unconditionally (was default-OFF gated by
    # CLOSED_FLAT_INVARIANT_ENABLED, a safety invariant behind a default-off
    # flag = Prime-Directive anti-pattern). Alert-only — promotion to
    # auto-flatten remains a separate, deliberately-unbuilt step. The helper
    # never raises; the orphan reconciler above remains the safety net. See
    # ``docs/claude/closed-flat-invariant.md`` for the full design.
    from src.runtime._closed_flat_wiring import maybe_run_closed_flat_check
    maybe_run_closed_flat_check(db, summaries)

    return summaries


def run_monitor_tick(
    *,
    db_path: Optional[str] = None,
    ohlcv_fetcher: Optional[Callable[[str, Optional[str]], Any]] = None,
    strategies: Optional[List[str]] = None,
    strategy_cfg: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """BACK-COMPAT WRAPPER — both halves, in the original order.

    Every existing caller (`src/main.py`, tests, ops scripts) keeps working and
    behaves EXACTLY as before: same phases, same order, one tick. The split
    above is what makes the exit half schedulable on its own cadence; nothing
    is decoupled until a caller actually calls the halves separately, so this
    commit is a refactor with no behavioural change.
    """
    summaries = run_exit_evaluation_tick(
        db_path=db_path, ohlcv_fetcher=ohlcv_fetcher,
        strategies=strategies, strategy_cfg=strategy_cfg,
    )
    summaries.update(run_reconciliation_tick(
        db_path=db_path, ohlcv_fetcher=ohlcv_fetcher,
        strategies=strategies, strategy_cfg=strategy_cfg,
    ))
    return summaries
