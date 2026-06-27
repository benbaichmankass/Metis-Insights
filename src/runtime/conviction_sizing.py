"""Conviction-driven sizing — P2 of the unified-confidence risk redesign.

**Advisory / observe-only — no gate.** This computes the *would-be*
conviction-driven size for every order and logs it to a soak log, but it
**always returns the RiskManager-computed qty UNCHANGED**. It never touches
money — exactly like the P1 ``meta.conviction`` stamp, it just accrues the
evidence (the would-be conviction size vs the actual risk-based size) so the
distribution can be reviewed before conviction sizing graduates to actually
driving the order.

The **annotator** (``annotate_conviction_sizing`` / ``compute_conviction_sizing``)
deliberately has **no on/off flag** (no ``*_MODE``, no ``*_ENABLED``, no
allowlist). A default-off gate in front of the observe-only annotator would be
the stranding trap the Prime Directive forbids — it stays flagless and always-on.

Graduating conviction to an *actual* order-size influence is a SEPARATE apply
path (``apply_conviction_sizing``, Design B — graduate conviction from soak to
live, 2026-06-27) gated by ``CONVICTION_SIZING_MODE`` (off / annotate / apply,
default off). The flag gates a genuine reductive/symmetric influence — exactly
the role ``NEWS_INFLUENCE_MODE`` plays — not the observe-only annotator; this
reconciles the 2026-06-16 ``CONVICTION_SIZING_MODE`` rejection (which was about
gating the annotator). Default-off means deploying the apply path is a
byte-for-byte no-op on the order path; the flagless annotator soak runs
unchanged regardless.

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
    effective_risk_pct: float | None = None,
    settings: dict | None = None,
) -> float:
    """Graduate conviction sizing to an ACTUAL order-size influence — Design B.

    This is the NEW apply path, **separate** from ``annotate_conviction_sizing``
    (which stays flagless observe-only and runs unchanged). It is gated by
    ``CONVICTION_SIZING_MODE`` (off / annotate / apply, default off) +
    ``CONVICTION_SIZING_ACCOUNTS`` (allowlist, empty=all) +
    ``CONVICTION_SIZING_DIRECTION`` (reductive / symmetric, default reductive).

    Behaviour:
      * ``mode off`` (default) → return ``sized_qty`` UNCHANGED (byte-for-byte
        no-op on the order path).
      * account not in the allowlist → unchanged.
      * ``mode annotate`` → compute the would-be conviction size (reuse
        ``compute_conviction_sizing``), stamp it on ``pkg.meta`` as
        ``conviction_apply_decision`` + log, return ``sized_qty`` UNCHANGED.
      * ``mode apply``:
          - ``direction reductive`` → ``final = min(conviction_qty, sized_qty)``
            (never enlarges).
          - ``direction symmetric`` → may exceed ``sized_qty`` but is HARD-bounded
            by the 2% budget + margin cap already enforced in
            ``compute_conviction_sizing``.
        A conviction ``< NO_TRADE_FLOOR`` (default 0.0, inert) journals a
        per-trade refusal and returns ``0`` — a refusal, NOT a gate flip.

    Daily-loss clamp: ``effective_risk_pct`` is the account's effective
    post-daily-loss risk fraction. When provided, the conviction size is clamped
    so its implied risk fraction can't exceed ``min(PER_TRADE_RISK_BUDGET,
    effective_risk_pct)`` — so a daily-loss-throttled account can't be
    re-inflated by a high-conviction trade. ``None`` skips the extra clamp.

    Fail-inert: any exception, a ``sized_qty <= 0`` (RiskManager refusal), or a
    missing/None conviction on the package → the incoming ``sized_qty`` is
    returned UNCHANGED (mirrors ``apply_news_downsize``).
    """
    try:
        if sized_qty is None or sized_qty <= 0:
            return sized_qty

        from src.runtime.runtime_flags import (
            _conviction_sizing_accounts,
            _conviction_sizing_direction,
            _conviction_sizing_mode,
        )

        mode = _conviction_sizing_mode(settings)
        if mode == "off":
            return sized_qty

        allowlist = _conviction_sizing_accounts(settings)
        if allowlist and account_name not in allowlist:
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
        # No would-be size to act on (missing conviction / un-computable inputs)
        # → fail-inert, return the RiskManager qty unchanged.
        if would_be is None:
            return sized_qty

        # Below-floor → journaled per-trade refusal + qty 0 (NOT a gate flip).
        if record.get("action") == "no_trade_floor":
            if mode == "apply":
                _journal_below_floor_refusal(pkg, account_name, record)
                _log_conviction_apply(
                    pkg, account_name, sized_qty, 0.0, mode,
                    "reductive", record, applied=True,
                )
                return 0.0
            # annotate: stamp the decision, do not resize.
            _stamp_apply_decision(pkg, record, mode, "reductive", sized_qty, sized_qty)
            _log_conviction_apply(
                pkg, account_name, sized_qty, sized_qty, mode,
                "reductive", record, applied=False,
            )
            return sized_qty

        direction = _conviction_sizing_direction(settings)
        conviction_qty = float(would_be)

        # Daily-loss clamp — cap the conviction-implied risk fraction to
        # min(2%, effective_risk_pct). The conviction qty implies a risk fraction
        # of ``conviction × PER_TRADE_RISK_BUDGET``; ``record['risk_qty']`` is the
        # full-budget (2%) qty, so scale it by the capped fraction / 2% to get the
        # max qty the daily-loss-aware risk fraction permits.
        clamp_note: dict | None = None
        if effective_risk_pct is not None:
            try:
                eff = float(effective_risk_pct)
                risk_qty = float(record.get("risk_qty") or 0.0)
                if eff >= 0 and PER_TRADE_RISK_BUDGET > 0 and risk_qty > 0:
                    capped_pct = min(PER_TRADE_RISK_BUDGET, eff)
                    cap_qty = (capped_pct / PER_TRADE_RISK_BUDGET) * risk_qty
                    is_futures = str(market_type or "spot").lower() == "futures"
                    eff_precision = 0 if is_futures else qty_precision
                    cap_qty = _floor_to_step_local(cap_qty, eff_precision)
                    if is_futures and cap_qty < 1.0:
                        cap_qty = 0.0
                    if conviction_qty > cap_qty:
                        clamp_note = {
                            "effective_risk_pct": eff,
                            "capped_pct": capped_pct,
                            "cap_qty": cap_qty,
                            "pre_clamp_qty": conviction_qty,
                        }
                        conviction_qty = cap_qty
            except (TypeError, ValueError):
                pass  # fail-inert: a bad fraction never inflates the order

        if mode == "annotate":
            _stamp_apply_decision(
                pkg, record, mode, direction, sized_qty, sized_qty,
                clamp_note=clamp_note,
            )
            _log_conviction_apply(
                pkg, account_name, sized_qty, sized_qty, mode,
                direction, record, applied=False, clamp_note=clamp_note,
            )
            return sized_qty

        # mode == "apply"
        if direction == "reductive":
            final = min(conviction_qty, sized_qty)
        else:  # symmetric — may enlarge, hard-bounded by the 2% budget + margin
            final = conviction_qty
        if final < 0:
            final = 0.0

        _stamp_apply_decision(
            pkg, record, mode, direction, sized_qty, final, clamp_note=clamp_note,
        )
        _log_conviction_apply(
            pkg, account_name, sized_qty, final, mode, direction, record,
            applied=True, clamp_note=clamp_note,
        )
        logger.info(
            "conviction_apply(%s/%s) strategy=%s account=%s conviction=%.4f "
            "qty %.8f -> %.8f",
            mode, direction, getattr(pkg, "strategy", "?"), account_name,
            record.get("conviction", 0.0), sized_qty, final,
        )
        return final
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "apply_conviction_sizing failed (qty unchanged): %s", exc
        )
        return sized_qty


def _floor_to_step_local(value: float, precision: int) -> float:
    """Floor ``value`` to ``precision`` decimals via risk.py's canonical helper."""
    from src.units.accounts.risk import _floor_to_step

    return _floor_to_step(value, precision)


def _stamp_apply_decision(
    pkg: Any,
    record: dict,
    mode: str,
    direction: str,
    sized_qty: float,
    final_qty: float,
    *,
    clamp_note: dict | None = None,
) -> None:
    """Stamp the apply-path decision on ``pkg.meta`` (best-effort, no raise)."""
    meta = getattr(pkg, "meta", None)
    if not isinstance(meta, dict):
        return
    decision = dict(record)
    decision.update(
        {
            "mode": mode,
            "direction": direction,
            "risk_based_qty": sized_qty,
            "final_qty": final_qty,
            "resized": final_qty != sized_qty,
        }
    )
    if clamp_note is not None:
        decision["daily_loss_clamp"] = clamp_note
    meta["conviction_apply_decision"] = decision


def _journal_below_floor_refusal(pkg: Any, account_name: str, record: dict) -> None:
    """Journal a per-trade below-floor conviction refusal (best-effort, no raise).

    A conviction below ``NO_TRADE_FLOOR`` means the trade isn't worth taking — a
    clean per-trade refusal (``status='rejected'``), never a gate/mode flip.
    """
    try:
        from src.units.accounts.execute import log_rejection_to_journal

        conviction = record.get("conviction")
        floor = record.get("no_trade_floor")
        reason = (
            f"conviction_below_no_trade_floor: conviction={conviction} < "
            f"NO_TRADE_FLOOR={floor} (account={account_name}) — conviction sizing "
            "refused this trade as below the no-trade floor; the account stays "
            "live, the next signal is sized fresh."
        )
        log_rejection_to_journal(
            pkg,
            {"account_id": account_name},
            reason=reason,
            status="rejected",
            sized_qty=0.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_journal_below_floor_refusal: could not journal refusal: %s", exc
        )


def _log_conviction_apply(
    pkg: Any,
    account_name: str,
    risk_based_qty: float,
    final_qty: float,
    mode: str,
    direction: str,
    record: dict,
    *,
    applied: bool,
    clamp_note: dict | None = None,
) -> None:
    """Append the apply-path decision to the conviction soak log (best-effort).

    Reuses the same ``conviction_sizing.jsonl`` soak log as the annotator but
    tags the row ``kind='apply'`` so the two streams are distinguishable.
    """
    try:
        from src.utils.paths import runtime_logs_dir

        path = runtime_logs_dir() / "conviction_sizing.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "apply",
            "mode": mode,
            "direction": direction,
            "applied": applied,
            "strategy": str(getattr(pkg, "strategy", "") or ""),
            "symbol": str(getattr(pkg, "symbol", "") or ""),
            "account": account_name,
            "conviction": record.get("conviction"),
            "risk_based_qty": risk_based_qty,
            "final_qty": final_qty,
            "daily_loss_clamp": clamp_note,
            "decision": record,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, default=str) + "\n")
    except OSError as exc:
        logger.warning("_log_conviction_apply: could not write soak log: %s", exc)


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
