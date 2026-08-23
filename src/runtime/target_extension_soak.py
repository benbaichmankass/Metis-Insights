"""Target-extension ANNOTATE soak — the exit-geometry rebuild's evidence trail.

`_base.monitor` has declared `{"tp": float}` — move the take-profit — since it
was written, and **no strategy has ever produced one** (AST-verified across
every module in `src/units/strategies/`, 2026-08-23). Everything downstream is
already live: `monitor_verdict.interpret_verdict` parses a `tp` delta
independently of `sl`, `order_monitor._apply_update` routes it,
`_send_modify_to_exchange` forwards it, and `execute.modify_open_order` amends
the resting leg on Bybit / IB / Alpaca. Only the producer was missing.

This is the producer's **observe-only** first phase, mirroring the M20
stale-stop rollout exactly (`exit_lever_soak`): the monitor evaluates the
extension decision every tick and, when it *would* move a target, writes one
row here **instead of returning a `tp` verdict**. Nothing reads it back. It is
the evidence trail for the Tier-3 flip, not an input to any decision.

⚠️ **THE ROW CARRIES THE EXPECTATION STATE, and that is the point.** A soak
that only logged would-extend events would go silent for the wrong reason: 29
of 52 enabled legs declare `tp_r >= 50`, so they have **no expectation to
extend from**, and their silence would read as *"the lever never fires"* when
it means *"there was never a target"*. Every evaluated tick that reaches the
approach test logs its `expectation_state` and `extension_state` together, so
the two are never confused.

Pure record builder (never raises → `None`) + best-effort append-only writer to
`runtime_logs/target_extension_soak.jsonl`. Deduped in-process per
`(order_package_id, extension_state, extends_so_far)` so a persistent condition
logs once per trade per state per process (a restart may re-log once — harmless
for an audit log).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SOAK_LOG_NAME = "target_extension_soak.jsonl"

# In-process dedup: one row per (order_package_id, extension_state, n_extends).
_ANNOTATED: set = set()

# States worth a row. `not_approaching` is deliberately EXCLUDED — it is the
# ordinary state of almost every open trade on almost every tick, and logging
# it would bury the informative rows under noise (the desensitized-alarm shape,
# applied to a log rather than an alert).
_LOGGED_STATES = frozenset({
    "extend", "thesis_broken_hold", "thesis_unknown",
    "extension_cap_reached", "no_expectation_declared",
})


def soak_log_path():
    from src.utils.paths import runtime_logs_dir

    return runtime_logs_dir() / SOAK_LOG_NAME


def record_target_extension(
    *,
    strategy: str,
    symbol: str,
    direction: str,
    order_package_id: Any = None,
    expectation: Optional[Dict[str, Any]] = None,
    extension: Optional[Dict[str, Any]] = None,
    price: Any = None,
    entry: Any = None,
    current_tp: Any = None,
    thesis: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Append one observe-only target-extension row (best-effort).

    Returns the record, or ``None`` when the state is not worth logging /
    deduped / unwritable. **Never raises** — it is called from the live monitor.
    """
    try:
        ext_state = str((extension or {}).get("state") or "")
        if ext_state not in _LOGGED_STATES:
            return None
        key = (str(order_package_id or ""), ext_state,
               int((extension or {}).get("extends_so_far") or 0))
        if key in _ANNOTATED:
            return None

        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "order_package_id": order_package_id,
            "strategy": strategy,
            "symbol": symbol,
            "direction": direction,
            # The two states TOGETHER — see the module docstring.
            "expectation_state": (expectation or {}).get("state"),
            "extension_state": ext_state,
            "target_r": (expectation or {}).get("target_r"),
            "target_source_key": (expectation or {}).get("source_key"),
            "cap_r": (expectation or {}).get("cap_r"),
            "expectation_price": (expectation or {}).get("expectation_price"),
            "placed_price": (expectation or {}).get("placed_price"),
            "current_tp": current_tp,
            "would_move_tp_to": (extension or {}).get("new_target"),
            "extends_so_far": (extension or {}).get("extends_so_far"),
            "progress_frac": (extension or {}).get("progress_frac"),
            "price": price,
            "entry": entry,
            # How the thesis verdict was reached, so a `thesis_unknown` row can
            # be told apart from one where the predicate ran and said no.
            "thesis": thesis,
            "observe_only": True,
        }
        _ANNOTATED.add(key)
        path = soak_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.info(
            "target_extension_soak: %s %s %s — expectation=%s extension=%s "
            "(observe-only, no order changed)",
            strategy, symbol, direction,
            rec["expectation_state"], ext_state,
        )
        return rec
    except Exception as exc:  # noqa: BLE001 — never break the monitor
        logger.debug("target_extension_soak: record failed: %s", exc)
        return None


def annotate_from_monitor(
    *,
    strategy: str,
    open_pkg: Dict[str, Any],
    meta: Dict[str, Any],
    price: Any,
    thesis_intact: Optional[bool],
    thesis: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """One call a strategy's ``monitor()`` makes; returns nothing actionable.

    Resolves the trade's declared expectation from ``meta`` (NOT ``cfg`` —
    ``run_monitor_tick`` passes ``cfg={}`` in production, so ``meta`` is the
    only channel a live monitor reliably sees) and evaluates the extension,
    then records it. **Never raises and never returns a verdict.**
    """
    try:
        from src.runtime.target_expectation import (
            evaluate_extension, resolve_expectation,
        )
        direction = str(open_pkg.get("direction") or "").lower()
        entry = open_pkg.get("entry")
        expectation = resolve_expectation(
            meta, entry=entry, sl=open_pkg.get("sl"), direction=direction,
        )
        extension = evaluate_extension(
            expectation, price=price, entry=entry, direction=direction,
            thesis_intact=thesis_intact,
        )
        return record_target_extension(
            strategy=strategy,
            symbol=str(open_pkg.get("symbol") or ""),
            direction=direction,
            order_package_id=open_pkg.get("order_package_id"),
            expectation=expectation,
            extension=extension,
            price=price,
            entry=entry,
            current_tp=open_pkg.get("tp"),
            thesis=thesis,
        )
    except Exception as exc:  # noqa: BLE001 — never break the monitor
        logger.debug("target_extension_soak: annotate failed: %s", exc)
        return None
