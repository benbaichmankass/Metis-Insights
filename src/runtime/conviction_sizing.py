"""Conviction-driven sizing — P2 of the unified-confidence risk redesign.

**Advisory / observe-only — no gate.** This computes the *would-be*
conviction-driven size for every order and logs it to a soak log, but it
**always returns the RiskManager-computed qty UNCHANGED**. It never touches
money — exactly like the P1 ``meta.conviction`` stamp, it just accrues the
evidence (the would-be conviction size vs the actual risk-based size) so the
distribution can be reviewed before conviction sizing graduates to actually
driving the order.

There is deliberately **no on/off flag** (no ``*_MODE``, no ``*_ENABLED``, no
allowlist). A default-off gate in front of this would be the stranding trap the
Prime Directive forbids. When conviction graduates to *actually* sizing orders,
that is a deliberate change to the sizing path itself — governed by the existing
account ``mode`` gate and the margin / daily-loss guards like every other order
behaviour — not the flip of a dormant switch installed here in advance.

Computation (design § 3.3 / § 10): ``conviction × per_trade_risk_budget (2%)``
bounded above by the available-margin ceiling and a proportional free-margin
throttle. Risk basis is the **free balance** (matches
``risk.py::_size_unbounded`` so the would-be number is a true drop-in
comparison for today's ``effective_risk_pct`` sizing). Reads the observe-only
``meta.conviction`` stamped at signal time.

Fail-permissive: any error / missing conviction / un-computable inputs → the
``sized_qty`` is returned unchanged and nothing is logged.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# --- decided P2 numbers (design § 6 #1, § 10) ----------------------------- #
# Max risk fraction a conviction=1.0 trade would take; conviction scales within
# it. DECIDED 2026-06-16 = 2% (reached only at full conviction).
PER_TRADE_RISK_BUDGET = 0.02
# No-trade floor read off the final conviction (inert at 0 — a deliberate raise
# is a future decision once conviction actually drives sizing).
NO_TRADE_FLOOR = 0.0
# Margin-safety headroom for the buffer-fallback ceiling (mirrors
# risk.py::_MARGIN_SAFETY_BUFFER so the two ceilings agree).
_MARGIN_SAFETY_BUFFER = 0.9


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _conviction_from_pkg(pkg: Any) -> float | None:
    """Read the observe-only calibrated conviction stamped at signal time.

    ``pkg.meta['conviction']['conviction']`` is the calibrated v1 blend; ``None``
    / absent → no would-be size (returns ``None``).
    """
    meta = getattr(pkg, "meta", None)
    if not isinstance(meta, dict):
        return None
    block = meta.get("conviction")
    if not isinstance(block, dict):
        return None
    val = block.get("conviction")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def compute_conviction_sizing(
    pkg: Any,
    sized_qty: float,
    *,
    balance_usd: float | None = None,
    available_usd: float | None = None,
    total_account_usd: float | None = None,
    leverage: int = 0,
    market_type: str = "spot",
    min_qty: float = 0.0,
    qty_precision: int = 3,
) -> tuple[float | None, dict]:
    """Resolve the would-be conviction-sized qty (``desired`` clamped to
    feasibility).

    Returns ``(would_be_qty, record)``. ``would_be_qty is None`` means "no
    would-be size" (missing conviction / un-computable inputs). Pure — never
    raises (returns ``(None, {...})`` on any failure) and never mutates state.
    """
    from src.units.accounts.risk import _floor_to_step, contract_value_usd_for

    conviction = _conviction_from_pkg(pkg)
    if conviction is None:
        return None, {"action": "no_conviction"}
    conviction = _clip01(conviction)

    # No-trade floor (inert at 0): below it the trade wouldn't be worth taking.
    if conviction < NO_TRADE_FLOOR:
        return 0.0, {
            "action": "no_trade_floor",
            "conviction": conviction,
            "no_trade_floor": NO_TRADE_FLOOR,
        }

    entry = float(getattr(pkg, "entry", 0.0) or 0.0)
    sl = float(getattr(pkg, "sl", 0.0) or 0.0)
    risk_distance = abs(entry - sl)
    if risk_distance <= 0 or entry <= 0:
        return None, {"action": "degenerate_levels"}

    # Risk basis: the SAME basis risk.py::_size_unbounded applies the risk
    # fraction to — the free balance (``balance_usd``), NOT total equity. Keeps
    # the would-be 2% number a true drop-in comparison for ``effective_risk_pct``.
    risk_basis = balance_usd if balance_usd is not None else total_account_usd
    if not risk_basis or risk_basis <= 0:
        return None, {"action": "no_equity_basis"}

    symbol = str(getattr(pkg, "symbol", "") or "")
    cvu = contract_value_usd_for(symbol) or 1.0
    is_futures = str(market_type or "spot").lower() == "futures"

    risk_usd = PER_TRADE_RISK_BUDGET * float(risk_basis)
    risk_qty = risk_usd / (risk_distance * cvu)
    desired = conviction * risk_qty

    # Margin ceiling (hard upper bound). Skipped for futures — futures margin is
    # per-contract SPAN, not price×qty/leverage (same carve-out as risk.py). The
    # buffer-fallback basis prefers total equity (matches risk.py's cross-margin).
    eff_lev = leverage if leverage and leverage > 0 else 1
    margin_basis = total_account_usd if total_account_usd is not None else balance_usd
    margin_cap: float | None = None
    if not is_futures and entry > 0:
        if available_usd is not None and available_usd >= 0:
            margin_cap = (float(available_usd) * eff_lev) / entry
        elif margin_basis and margin_basis > 0:
            margin_cap = (float(margin_basis) * eff_lev * _MARGIN_SAFETY_BUFFER) / entry

    # Proportional free-margin throttle (§ 3.3). 1.0 when the figures aren't both
    # available.
    throttle = 1.0
    if (
        available_usd is not None
        and total_account_usd is not None
        and total_account_usd > 0
    ):
        throttle = _clip01(float(available_usd) / float(total_account_usd))

    final = desired * throttle
    if margin_cap is not None:
        final = min(final, margin_cap)
    if final < 0:
        final = 0.0

    eff_precision = 0 if is_futures else qty_precision
    final = _floor_to_step(final, eff_precision)
    if is_futures and final < 1.0:  # whole-contract refusal (mirrors risk.py)
        final = 0.0

    record = {
        "action": "would_be_size",
        "conviction": conviction,
        "risk_budget": PER_TRADE_RISK_BUDGET,
        "risk_qty": risk_qty,
        "desired": desired,
        "throttle": throttle,
        "margin_cap": margin_cap,
        "would_be_qty": final,
        "risk_based_qty": sized_qty,
        "no_trade_floor": NO_TRADE_FLOOR,
        "market_type": market_type,
    }
    return final, record


def annotate_conviction_sizing(
    pkg: Any,
    sized_qty: float,
    *,
    account_name: str = "",
    balance_usd: float | None = None,
    available_usd: float | None = None,
    total_account_usd: float | None = None,
    leverage: int = 0,
    market_type: str = "spot",
    min_qty: float = 0.0,
    qty_precision: int = 3,
) -> float:
    """Compute + log the would-be conviction size; **always return ``sized_qty``
    unchanged** (advisory / observe-only).

    No gate, no flag — this runs on every order and only accrues soak evidence
    (`runtime_logs/conviction_sizing.jsonl`). It never resizes. Never raises — on
    any error / missing conviction the qty is returned unchanged and nothing is
    logged. A ``sized_qty <= 0`` (a RiskManager refusal) is left untouched and
    not annotated.
    """
    try:
        if sized_qty is None or sized_qty <= 0:
            return sized_qty
        would_be, record = compute_conviction_sizing(
            pkg,
            sized_qty,
            balance_usd=balance_usd,
            available_usd=available_usd,
            total_account_usd=total_account_usd,
            leverage=leverage,
            market_type=market_type,
            min_qty=min_qty,
            qty_precision=qty_precision,
        )
        if would_be is None:
            return sized_qty  # no would-be size to record

        meta = getattr(pkg, "meta", None)
        if isinstance(meta, dict):
            meta["conviction_sizing_decision"] = record
        _log_conviction_sizing(pkg, account_name, sized_qty, would_be, record)
        logger.debug(
            "conviction_sizing(observe) strategy=%s account=%s conviction=%.4f "
            "risk_qty=%.8f would_be=%.8f (qty unchanged)",
            getattr(pkg, "strategy", "?"),
            account_name,
            record.get("conviction", 0.0),
            sized_qty,
            would_be,
        )
        return sized_qty  # advisory — NEVER changes the order
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "annotate_conviction_sizing failed (qty unchanged): %s", exc
        )
        return sized_qty


def _log_conviction_sizing(
    pkg: Any,
    account_name: str,
    risk_based_qty: float,
    would_be_qty: float,
    record: dict,
) -> None:
    """Append the would-be conviction-sizing decision to the soak log (best-effort)."""
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / "conviction_sizing.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "strategy": str(getattr(pkg, "strategy", "") or ""),
            "symbol": str(getattr(pkg, "symbol", "") or ""),
            "account": account_name,
            "conviction": record.get("conviction"),
            "risk_based_qty": risk_based_qty,
            "would_be_qty": would_be_qty,
            "decision": record,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except OSError as exc:
        logger.warning("_log_conviction_sizing: could not write soak log: %s", exc)
