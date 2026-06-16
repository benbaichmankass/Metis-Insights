"""Conviction-driven sizing — P2 of the unified-confidence risk redesign.

Reads the **observe-only** ``meta.conviction`` already stamped on every order
package (P1, ``src/runtime/conviction.py`` + ``conviction_inputs.py``) and turns
it into a size scalar that ``Coordinator.multi_account_execute`` applies to the
RiskManager-computed per-account qty — sitting beside ``apply_advisory_downsize``
/ ``apply_news_downsize`` at the same post-sizing multiplier site.

**KEY difference from the advisory/news hooks:** those are *reductive only*
(``[floor, 1.0]``). Conviction sizing can **enlarge** — it replaces the flat
``effective_risk_pct`` with ``conviction × per_trade_risk_budget`` (2%, the max a
``conviction=1.0`` trade may take), bounded above by the available-margin ceiling
and a proportional free-margin throttle (design § 3.3 / § 10). Because it can grow
the order, it is **demo-scoped** (``CONVICTION_SIZING_ACCOUNTS`` must name the
account) and **annotate-first**.

Flag (mirrors ``NEWS_INFLUENCE_MODE`` — NOT a ``*_ENABLED`` gate, env-gate-guard):

  * ``CONVICTION_SIZING_MODE`` ∈ ``off`` (default) | ``annotate`` (log the
    would-be resize, never change qty) | ``apply`` (resize).
  * ``CONVICTION_SIZING_ACCOUNTS`` — comma-separated allowlist. For P2 it must
    name the demo account (``bybit_1``); an **empty** allowlist is a no-op
    (permissive-when-unset is deferred until after real-money sign-off, mirroring
    ``POSITION_NETTING_GUARD_ACCOUNTS`` but starting strict).

**Fail-permissive:** any error / missing conviction / ``mode=off`` /
account-not-allowed / un-computable margin returns ``sized_qty`` unchanged — a
scoring failure never strands or distorts a live signal. A ``sized_qty <= 0``
(the RiskManager already refused, e.g. daily-loss cap exhausted) is returned as
is — conviction sizing never resurrects a refused trade.

Rollback is one env flip (``CONVICTION_SIZING_MODE=off``), no redeploy.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# --- decided / inert P2 numbers (design § 6 #1, § 10) ---------------------- #
# Max risk fraction a conviction=1.0 trade may take; conviction scales *within*
# this. DECIDED 2026-06-16 = 2% (the ceiling, reached only at full conviction).
PER_TRADE_RISK_BUDGET = 0.02
# No-trade floor read off the final conviction. Ships at 0 (inert) — a deliberate
# raise is an operator decision (open number, design § 6 #2).
NO_TRADE_FLOOR = 0.0
# Margin-safety headroom for the buffer-fallback ceiling (mirrors
# risk.py::_MARGIN_SAFETY_BUFFER so the two ceilings agree).
_MARGIN_SAFETY_BUFFER = 0.9


def _clip01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _conviction_from_pkg(pkg: Any) -> float | None:
    """Read the observe-only calibrated conviction stamped at signal time.

    ``pkg.meta['conviction']['conviction']`` is the calibrated v1 blend; ``None``
    / absent → no-op signal (returns ``None``).
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
    """Resolve the conviction-sized qty (``desired`` clamped to feasibility).

    Returns ``(final_qty, record)``. ``final_qty is None`` means "no decision"
    (missing conviction / un-computable inputs) — the caller keeps ``sized_qty``.
    Never raises — any failure yields ``(None, {...})``.
    """
    from src.units.accounts.risk import _floor_to_step, contract_value_usd_for

    conviction = _conviction_from_pkg(pkg)
    if conviction is None:
        return None, {"action": "no_conviction"}
    conviction = _clip01(conviction)

    # No-trade floor (inert at 0): below it the trade isn't worth taking.
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
    # fraction to — the free balance (``balance_usd``), NOT total equity. This
    # keeps the 2% budget a true drop-in replacement for ``effective_risk_pct``
    # (cross-margin total equity is only used for the margin-ceiling fallback).
    risk_basis = balance_usd if balance_usd is not None else total_account_usd
    if not risk_basis or risk_basis <= 0:
        return None, {"action": "no_equity_basis"}

    symbol = str(getattr(pkg, "symbol", "") or "")
    cvu = contract_value_usd_for(symbol) or 1.0
    is_futures = str(market_type or "spot").lower() == "futures"

    # risk_qty at the full 2% budget; conviction scales within it.
    risk_usd = PER_TRADE_RISK_BUDGET * float(risk_basis)
    risk_qty = risk_usd / (risk_distance * cvu)
    desired = conviction * risk_qty

    # Margin ceiling (hard upper bound). Skipped for futures — futures margin is
    # per-contract SPAN, not price×qty/leverage, and the broker rejects at submit
    # (same carve-out as risk.py's crypto-only margin cap). The buffer-fallback
    # basis prefers total equity (matches risk.py's cross-margin fallback).
    eff_lev = leverage if leverage and leverage > 0 else 1
    margin_basis = total_account_usd if total_account_usd is not None else balance_usd
    margin_cap: float | None = None
    if not is_futures and entry > 0:
        if available_usd is not None and available_usd >= 0:
            margin_cap = (float(available_usd) * eff_lev) / entry
        elif margin_basis and margin_basis > 0:
            margin_cap = (float(margin_basis) * eff_lev * _MARGIN_SAFETY_BUFFER) / entry

    # Proportional free-margin throttle (§ 3.3): the book self-damps as free
    # margin fills. 1.0 (no throttle) when the figures aren't both available.
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
    # Whole-contract refusal for futures (mirrors risk.py): sub-1-contract → 0.
    if is_futures and final < 1.0:
        final = 0.0

    record = {
        "action": "size",
        "conviction": conviction,
        "risk_budget": PER_TRADE_RISK_BUDGET,
        "risk_qty": risk_qty,
        "desired": desired,
        "throttle": throttle,
        "margin_cap": margin_cap,
        "final": final,
        "no_trade_floor": NO_TRADE_FLOOR,
        "market_type": market_type,
    }
    return final, record


def apply_conviction_sizing(
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
    """Resize a RiskManager-computed per-account qty by conviction.

    No-op (returns ``sized_qty`` unchanged) when: ``CONVICTION_SIZING_MODE`` is
    ``off``; ``account_name`` is not in ``CONVICTION_SIZING_ACCOUNTS``;
    ``sized_qty <= 0`` (already refused); conviction is missing; or the sizing is
    un-computable. In ``annotate`` mode the would-be ``final`` is logged but
    ``sized_qty`` is returned unchanged; only ``apply`` returns ``final``.

    Never raises — on any error the qty is returned unchanged.
    """
    try:
        from src.runtime.runtime_flags import (
            _conviction_sizing_accounts,
            _conviction_sizing_mode,
        )

        mode = _conviction_sizing_mode({})
        if mode == "off":
            return sized_qty
        # Demo-scoped: empty allowlist is a no-op for P2 (permissive-when-unset
        # is deferred until real-money sign-off). Account must be named.
        allow = _conviction_sizing_accounts({})
        if not allow or account_name not in allow:
            return sized_qty
        if sized_qty is None or sized_qty <= 0:
            return sized_qty

        final, record = compute_conviction_sizing(
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
        if final is None:
            return sized_qty  # no decision — keep the risk-based qty

        record["mode"] = mode
        record["intended_qty"] = sized_qty
        meta = getattr(pkg, "meta", None)
        if isinstance(meta, dict):
            meta["conviction_sizing_decision"] = record

        if mode == "annotate":
            _log_conviction_sizing(pkg, account_name, sized_qty, final, record)
            return sized_qty  # annotate NEVER changes the qty

        # apply
        _log_conviction_sizing(pkg, account_name, sized_qty, final, record)
        logger.info(
            "conviction_sizing strategy=%s account=%s conviction=%.4f qty %.8f -> %.8f",
            getattr(pkg, "strategy", "?"),
            account_name,
            record.get("conviction", 0.0),
            sized_qty,
            final,
        )
        return final
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apply_conviction_sizing failed (returning unchanged qty): %s", exc
        )
        return sized_qty


def _log_conviction_sizing(
    pkg: Any,
    account_name: str,
    intended_qty: float,
    final_qty: float,
    record: dict,
) -> None:
    """Append the conviction-sizing decision to the soak log (best-effort)."""
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / "conviction_sizing.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mode": record.get("mode"),
            "strategy": str(getattr(pkg, "strategy", "") or ""),
            "symbol": str(getattr(pkg, "symbol", "") or ""),
            "account": account_name,
            "conviction": record.get("conviction"),
            "intended_qty": intended_qty,
            "final_qty": final_qty,
            "would_be_qty": final_qty,
            "decision": record,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except OSError as exc:
        logger.warning("_log_conviction_sizing: could not write soak log: %s", exc)
