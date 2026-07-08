"""Canonical "clean trades" filters — the SINGLE source of truth for which
``trades`` rows count in the analytics endpoints.

Why this exists
---------------
The paper/real-money split predicate and the ``account_class`` wire helper were
copy-pasted across **8** router files (``dashboard``, ``performance``,
``order_packages``, ``trades_closed``, ``pnl``, ``pnl_history``, ``strategies``,
``attribution``). Every new rule (exclude reconciler artifacts, normalise a
unit, fix the close-time basis) then had to be applied in 8 places or it
drifted — the recurring "data-bug treadmill" (e.g. ``/performance`` and
``/stats`` disagreeing on real-money totals; reconciler ``orphan_adopt`` rows
polluting strategy KPIs). This module defines each rule ONCE so a consumer
composes its WHERE from these builders instead of re-deriving the SQL.

The builders take a column *prefix* (``""`` for a bare ``trades`` query,
``"t."`` for a joined query whose ``trades`` alias is ``t``) so the same logic
serves both shapes.

Semantics (preserved exactly from the prior per-router predicates):

* ``account_class`` is AUTHORITATIVE. For rows predating the column / backfill
  (``account_class IS NULL``) we fall back to the legacy ``is_demo`` boolean.
* "paper" selects only ``account_class='paper'`` (NOT ``prop`` — prop is a
  third, isolated funding class that never blends into paper or real KPIs).
* "not paper" (real-money) excludes BOTH ``paper`` and ``prop``.
"""
from __future__ import annotations

from typing import Any

# Reconciler / bookkeeping pseudo-strategies that are NOT real trading
# decisions and must never pollute strategy-performance KPIs. The reverse
# reconciler adopts an unexpected exchange position as a synthetic trade with
# ``strategy_name='orphan_adopt'`` / ``setup_type='adopted_orphan'`` — a
# recovery/bookkeeping state, not a strategy's trade. (The 2026-06-18 flap that
# turned one MGC position into 18 phantom losing trades, −$20,127, surfaced
# here as a fat negative ``orphan_adopt`` row in the real-money block.)
RECONCILER_PSEUDO_STRATEGIES = ("orphan_adopt",)


def _col(prefix: str, name: str) -> str:
    return f"{prefix}{name}" if prefix else name


def not_paper_predicate(prefix: str = "") -> str:
    """``AND``-able SQL fragment selecting REAL-MONEY rows (excludes paper + prop)."""
    ac = _col(prefix, "account_class")
    demo = _col(prefix, "is_demo")
    return (
        f" AND NOT (COALESCE({ac},'') IN ('paper','prop')"
        f" OR ({ac} IS NULL AND COALESCE({demo},0)=1))"
    )


def paper_predicate(prefix: str = "") -> str:
    """``AND``-able SQL fragment selecting ONLY paper-money rows (not prop)."""
    ac = _col(prefix, "account_class")
    demo = _col(prefix, "is_demo")
    return (
        f" AND (COALESCE({ac},'')='paper'"
        f" OR ({ac} IS NULL AND COALESCE({demo},0)=1))"
    )


def exclude_reconciler_predicate(prefix: str = "") -> str:
    """``AND``-able SQL fragment dropping reconciler-artifact rows from KPI
    aggregates. The pseudo-strategy names are a hard-coded constant tuple
    (never user input), so the inline literal carries no injection risk."""
    sn = _col(prefix, "strategy_name")
    names = ",".join(f"'{s}'" for s in RECONCILER_PSEUDO_STRATEGIES)
    return f" AND COALESCE({sn},'') NOT IN ({names})"


def exclude_superseded_predicate(prefix: str = "") -> str:
    """``AND``-able SQL fragment dropping ``reconcile_status='superseded'`` rows
    from analytics.

    A *superseded* row is a phantom orphan-flap DUPLICATE that the historical
    reconciliation pass (``scripts/ops/reconcile_orphan_history.py``, orphan-flap
    hardening #5) void-flagged in favour of the cluster's one canonical row —
    e.g. the 17 extra phantom MGC closes around the single real position. They
    are preserved in the journal as an audit trail (void-flag, not delete) but
    must never count toward PnL / win-rate / trade-count aggregates, or the
    fabricated PnL the flap wrote would still pollute the numbers.

    Distinct from :func:`exclude_reconciler_predicate`: that drops rows by the
    *pseudo-strategy name* of a bare adopt; this drops rows by the explicit
    terminal reconcile state, which also catches a phantom flap row whose
    ``strategy_name`` was reattached to a real strategy. NULL-safe via COALESCE
    so the overwhelming majority of rows (``reconcile_status IS NULL``) are
    kept. ``'superseded'`` is a hard-coded literal — no injection surface."""
    rs = _col(prefix, "reconcile_status")
    return f" AND COALESCE({rs},'') != 'superseded'"


def exclude_reset_flat_predicate(prefix: str = "") -> str:
    """``AND``-able SQL fragment dropping ``exit_reason='exchange_reset_flat'``
    rows from analytics.

    An ``exchange_reset_flat`` row is a position the position-snapshot
    reconciler closed as part of a **wholesale account RESET** (>= threshold
    positions vanishing from the exchange snapshot in one pass — the 2026-07-07
    alpaca_paper paper-account reset wiped all 8 at once). Those closes carry a
    real strategy name and a mark-to-market PnL, but they are NOT strategy exit
    decisions — the account was reset externally — so counting them would
    contaminate per-strategy win-rate / PnL. They stay in the journal (audit)
    but are excluded from KPI aggregates. NULL-safe; the literal is hard-coded
    (no injection surface). Operator-requested 2026-07-08."""
    er = _col(prefix, "exit_reason")
    return f" AND COALESCE({er},'') != 'exchange_reset_flat'"


def r_multiple(
    pnl: Any,
    entry_price: Any,
    stop_loss: Any,
    qty: Any,
    contract_value_usd: Any,
) -> "float | None":
    """Per-trade R-multiple ``pnl / risk_usd``, or ``None`` when risk is
    unknown / non-positive.

    ``risk_usd = |entry_price - stop_loss| * |qty| * contract_value_usd`` — the
    SAME absolute-USD scale as the stored multiplier-aware ``pnl`` (see
    ``src.runtime.local_pnl``), so R puts a tiny-notional crypto micro-trade and
    a multi-thousand-dollar futures contract on ONE comparable axis. This is the
    fix for the cross-instrument USD-blending in the raw ``totalPnl``/
    ``expectancy`` aggregates.

    Returns ``None`` — NEVER a raw-``pnl`` fallback — when any input is missing
    or the computed risk is ``<= 0`` (a flat/zero stop, missing size). Folding an
    un-normalised raw value into an R aggregate would re-introduce the exact
    blending bug, so the caller must treat ``None`` as "not R-measurable" and
    exclude it from the R numerator/denominator.
    """
    try:
        if pnl is None or entry_price is None or stop_loss is None or qty is None:
            return None
        risk = (
            abs(float(entry_price) - float(stop_loss))
            * abs(float(qty))
            * float(contract_value_usd or 0.0)
        )
        if risk <= 0:
            return None
        return float(pnl) / risk
    except (TypeError, ValueError):
        return None


def account_class_wire(account_class: Any, is_demo: Any) -> str:
    """Resolve a row's funding class for the wire: ``account_class`` when
    present, else the legacy ``is_demo`` boolean (rows predating the column /
    backfill). Never returns null — falls back to ``real_money``."""
    if account_class is not None and str(account_class).strip():
        return str(account_class).strip().lower()
    return "paper" if bool(is_demo) else "real_money"
