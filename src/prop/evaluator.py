"""Prop-firm evaluator — judge an equity curve + closed-trade ledger vs a ruleset.

Input: a :class:`~src.prop.ruleset.PropRuleset` + a portfolio run's FULL
timestamped equity curve and FULL closed-trade ledger (per design §5). Output:
a structured verdict dict.

The six checks (design §5) are evaluated **in time order so the FIRST breach
wins** and is returned with its timestamp:

  1. daily-loss        — any UTC day's drop from that day's start > limit.
  2. max-drawdown      — equity below the reference (static start / trailing
                         peak per ``drawdown_type``) by more than the limit.
  3. position-size     — any trade's entry notional > max_position_pct of equity.
  4. profit-target     — eval-pass: cumulative return reaches the target within
                         max_eval_days AND >= min_trading_days active days
                         (a *miss* is "eval not cleared", NOT a breach).
  5. consistency       — once the target is hit, any single day's realized profit
                         share of total profit > max_single_day_profit_share.
  6. funded-soak       — re-run checks 1-3 + 5 over funded_soak_days past the
                         eval-pass point ("would it then survive funded too").

Pure + deterministic: no network, no candle data, no clock reads — the unit
tests build synthetic curves/ledgers by hand and assert the breach.

Tier-1 research tooling — no live-path imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from src.prop.ruleset import PropRuleset

# An equity-curve point is (timestamp, equity_usd). The timestamp may be an ISO
# string (as backtest_system.py serializes it), a datetime, or a date.
_TS = Union[str, datetime, date]
EquityPoint = Tuple[_TS, float]


@dataclass
class TradeRecord:
    """One closed trade from the portfolio ledger (the subset the evaluator needs)."""

    owner: str
    entry_ts: _TS
    exit_ts: _TS
    pnl: float
    notional: float = 0.0  # entry notional (qty * entry price); 0 => skip position-size check


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------
def _to_dt(ts: _TS) -> datetime:
    """Coerce any accepted timestamp form to a tz-aware UTC datetime."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, date):
        return datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
    s = str(ts).strip()
    # Tolerate the common ISO variants the engine emits ("...+00:00", "...Z",
    # naive). pandas serializes Timestamps as "YYYY-MM-DD HH:MM:SS+00:00".
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # last-ditch: strip a trailing timezone-name or microseconds quirk
        dt = datetime.fromisoformat(s.split(".")[0])
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _utc_day(ts: _TS) -> date:
    return _to_dt(ts).date()


# ---------------------------------------------------------------------------
# The evaluator
# ---------------------------------------------------------------------------
def _breach(rule: str, ts: _TS, detail: str, **extra: Any) -> Dict[str, Any]:
    out = {"rule": rule, "ts": str(ts), "detail": detail}
    out.update(extra)
    return out


def _scan_equity_breaches(
    curve: Sequence[EquityPoint],
    ruleset: PropRuleset,
    account_size: float,
) -> Optional[Dict[str, Any]]:
    """Walk the equity curve once, in order, returning the FIRST daily-loss or
    max-drawdown breach (whichever happens earlier in time), else None.

    Both checks share one ordered pass so "first breach wins" is honoured
    across rule types, not just within one rule.
    """
    daily_loss_pct = ruleset.limits.daily_loss_pct
    max_dd_pct = ruleset.limits.max_drawdown_pct
    dd_type = ruleset.limits.drawdown_type

    day: Optional[date] = None
    day_start_eq: float = account_size
    peak: float = account_size

    for ts, eq in curve:
        eq = float(eq)
        d = _utc_day(ts)
        if d != day:
            day = d
            day_start_eq = eq
        peak = max(peak, eq)

        # (1) daily-loss: drop from this day's starting equity.
        if daily_loss_pct is not None and day_start_eq > 0:
            day_dd = (day_start_eq - eq) / day_start_eq
            if day_dd > daily_loss_pct + 1e-12:
                return _breach(
                    "daily_loss",
                    ts,
                    f"day {d} loss {day_dd * 100:.2f}% > {daily_loss_pct * 100:.2f}%",
                    day=str(d),
                    loss_pct=round(day_dd, 6),
                )

        # (2) max-drawdown: below the reference by more than the limit.
        if max_dd_pct is not None:
            ref = peak if dd_type == "trailing" else account_size
            if ref > 0:
                dd = (ref - eq) / ref
                if dd > max_dd_pct + 1e-12:
                    return _breach(
                        "max_drawdown",
                        ts,
                        f"{dd_type} DD {dd * 100:.2f}% > {max_dd_pct * 100:.2f}%",
                        depth_pct=round(dd, 6),
                        drawdown_type=dd_type,
                    )
    return None


def _position_size_breach(
    trades: Sequence[TradeRecord],
    ruleset: PropRuleset,
    account_size: float,
) -> Optional[Dict[str, Any]]:
    """First trade whose entry notional exceeded max_position_pct of the account.

    We size against the account_size (the prop limit reference) — entry-time
    running equity isn't on the ledger, and the prop firm's position cap is a
    fraction of the account, not of fluctuating equity.
    """
    cap = ruleset.limits.max_position_pct
    if cap is None or account_size <= 0:
        return None
    for t in sorted(trades, key=lambda x: _to_dt(x.entry_ts)):
        if t.notional <= 0:
            continue
        share = t.notional / account_size
        if share > cap + 1e-12:
            return _breach(
                "position_size",
                t.entry_ts,
                f"notional {share * 100:.2f}% of account > {cap * 100:.2f}% ({t.owner})",
                share=round(share, 6),
                owner=t.owner,
            )
    return None


def _eval_target(
    curve: Sequence[EquityPoint],
    trades: Sequence[TradeRecord],
    ruleset: PropRuleset,
    account_size: float,
) -> Dict[str, Any]:
    """Did the run clear the evaluation profit target within the window + active-days?

    Returns {passed, days_to_target, active_trading_days, target_ts}. A miss is
    NOT a breach — it's just "eval not cleared in window" (design §5.4).
    """
    target = ruleset.evaluation.profit_target_pct
    min_days = ruleset.evaluation.min_trading_days
    max_eval_days = ruleset.evaluation.max_eval_days

    # "active trading day" = a day on which at least one trade closed.
    active_days = {_utc_day(t.exit_ts) for t in trades}
    active_trading_days = len(active_days)

    if not curve:
        return {
            "passed": False,
            "days_to_target": None,
            "active_trading_days": active_trading_days,
            "target_ts": None,
        }

    start_dt = _to_dt(curve[0][0])
    target_ts: Optional[_TS] = None
    days_to_target: Optional[int] = None

    if target is not None:
        thresh = account_size * (1.0 + target)
        for ts, eq in curve:
            if float(eq) >= thresh:
                target_ts = ts
                days_to_target = (_to_dt(ts).date() - start_dt.date()).days
                break

    passed = target_ts is not None
    # min-trading-days gate
    if passed and active_trading_days < min_days:
        passed = False
    # max-eval-days window gate
    if passed and max_eval_days is not None and days_to_target is not None:
        if days_to_target > max_eval_days:
            passed = False

    return {
        "passed": passed,
        "days_to_target": days_to_target,
        "active_trading_days": active_trading_days,
        "target_ts": target_ts,
    }


def _per_day_profit(trades: Sequence[TradeRecord]) -> Dict[date, float]:
    out: Dict[date, float] = {}
    for t in trades:
        d = _utc_day(t.exit_ts)
        out[d] = out.get(d, 0.0) + float(t.pnl)
    return out


def _consistency_breach(
    trades: Sequence[TradeRecord],
    ruleset: PropRuleset,
) -> Tuple[Optional[Dict[str, Any]], Optional[float]]:
    """Check the single-day profit-share consistency rule.

    Returns (breach_or_None, worst_day_share). worst_day_share is reported even
    when the rule is disabled (it's a useful metric), but only triggers a breach
    when consistency.enabled is True. Total profit is the sum of POSITIVE daily
    PnL (the firm's "total profit" basis); a day's share is its positive PnL over
    that total. Days are scanned in date order so the first offending day wins.
    """
    if not trades:
        return None, None
    per_day = _per_day_profit(trades)
    total_profit = sum(p for p in per_day.values() if p > 0)
    if total_profit <= 0:
        return None, None

    worst_share = 0.0
    worst_day: Optional[date] = None
    breach: Optional[Dict[str, Any]] = None
    cap = ruleset.consistency.max_single_day_profit_share
    for d in sorted(per_day):
        p = per_day[d]
        if p <= 0:
            continue
        share = p / total_profit
        if share > worst_share:
            worst_share = share
            worst_day = d
        if ruleset.consistency.enabled and breach is None and share > cap + 1e-12:
            breach = _breach(
                "consistency",
                d,
                f"day {d} profit share {share * 100:.1f}% > {cap * 100:.1f}%",
                day=str(d),
                share=round(share, 6),
            )
    return breach, round(worst_share, 6) if worst_day is not None else None


def _slice_funded(
    curve: Sequence[EquityPoint],
    trades: Sequence[TradeRecord],
    target_ts: Optional[_TS],
    soak_days: int,
) -> Tuple[List[EquityPoint], List[TradeRecord], float]:
    """Slice the curve + ledger to the funded soak window: from the eval-pass
    point forward, up to soak_days. Returns (curve_slice, trades_slice,
    funded_start_equity)."""
    if target_ts is None or not curve:
        return [], [], 0.0
    start = _to_dt(target_ts)
    end = start + timedelta(days=soak_days)
    curve_slice = [(ts, eq) for ts, eq in curve if start <= _to_dt(ts) <= end]
    trades_slice = [t for t in trades if start <= _to_dt(t.exit_ts) <= end]
    funded_start_eq = float(curve_slice[0][1]) if curve_slice else float(curve[-1][1])
    return curve_slice, trades_slice, funded_start_eq


def evaluate(
    ruleset: PropRuleset,
    equity_curve: Sequence[EquityPoint],
    trades: Sequence[TradeRecord],
    *,
    account_size: Optional[float] = None,
    metrics: Optional[Dict[str, Any]] = None,
    roster: str = "",
) -> Dict[str, Any]:
    """Evaluate one portfolio run against ``ruleset`` and return a verdict dict.

    Verdict shape mirrors design §5:
        {ruleset, unconfirmed, roster, eval{...}, funded_soak{...},
         metrics{...}, headline}
    """
    acct = float(account_size) if account_size is not None else float(ruleset.account_size_usd)
    curve = list(equity_curve)
    ledger = list(trades)

    # --- EVAL phase: ordered first-breach across daily-loss + max-DD, then
    #     position-size, then profit-target, then consistency. ---
    eq_breach = _scan_equity_breaches(curve, ruleset, acct)
    ps_breach = _position_size_breach(ledger, ruleset, acct)
    target_info = _eval_target(curve, ledger, ruleset, acct)
    cons_breach, worst_share = _consistency_breach(ledger, ruleset)

    # First breach wins, ordered by timestamp across rule families. The
    # consistency rule only counts once the target is hit (design §5.5), so it
    # joins the race only when the eval passed.
    candidates: List[Dict[str, Any]] = []
    if eq_breach:
        candidates.append(eq_breach)
    if ps_breach:
        candidates.append(ps_breach)
    if cons_breach and target_info["passed"]:
        candidates.append(cons_breach)
    first_breach: Optional[Dict[str, Any]] = None
    if candidates:
        first_breach = min(candidates, key=lambda b: _to_dt(b["ts"]))

    eval_passed = target_info["passed"] and first_breach is None

    eval_block = {
        "passed": bool(eval_passed),
        "days_to_target": target_info["days_to_target"],
        "active_trading_days": target_info["active_trading_days"],
        "first_breach": first_breach,
    }

    # --- FUNDED soak: only meaningful if eval passed. Re-run checks 1-3 + 5
    #     over the post-target window. ---
    funded_block: Dict[str, Any] = {"survived": False, "first_breach": None}
    if eval_passed:
        f_curve, f_trades, f_start = _slice_funded(
            curve, ledger, target_info["target_ts"], ruleset.funded_soak_days
        )
        if f_curve:
            f_eq_breach = _scan_equity_breaches(f_curve, ruleset, f_start)
            f_ps_breach = _position_size_breach(f_trades, ruleset, acct)
            f_cons_breach, _ = _consistency_breach(f_trades, ruleset)
            f_cands = [b for b in (f_eq_breach, f_ps_breach, f_cons_breach) if b]
            f_first = min(f_cands, key=lambda b: _to_dt(b["ts"])) if f_cands else None
            funded_block = {"survived": f_first is None, "first_breach": f_first}
        else:
            # No data past the target — eval passed at the tail. Treat soak as
            # "not observed": survived True (nothing breached), flagged in detail.
            funded_block = {
                "survived": True,
                "first_breach": None,
                "note": "no_post_target_data",
            }

    # --- metrics: straight from the engine summary where available, plus the
    #     consistency worst-day share the engine doesn't compute. ---
    m = dict(metrics or {})
    m["consistency_worst_day_share"] = worst_share

    headline = _headline(eval_passed, funded_block, eval_block)

    return {
        "ruleset": ruleset.ruleset,
        "unconfirmed": ruleset.unconfirmed,
        "roster": roster,
        "eval": eval_block,
        "funded_soak": funded_block,
        "metrics": m,
        "headline": headline,
    }


def _headline(eval_passed: bool, funded_block: Dict[str, Any], eval_block: Dict[str, Any]) -> str:
    if not eval_passed:
        fb = eval_block.get("first_breach")
        if fb:
            return f"EVAL FAIL ({fb['rule']})"
        return "EVAL NOT REACHED (target not hit)"
    if funded_block.get("survived"):
        return "EVAL PASS / FUNDED SURVIVE"
    fb = funded_block.get("first_breach") or {}
    return f"EVAL PASS / FUNDED FAIL ({fb.get('rule', 'unknown')})"
