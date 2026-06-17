"""Monte-Carlo survival + speed module for prop-firm evaluation.

The single-historical-path evaluator (``src/prop/evaluator.py``) answers
"did THIS one realised price history clear the eval and survive the soak."
That is one draw from a distribution — it conflates the strategy's edge with
the particular order BTC happened to trend in. The operator's reframed
question is **probabilistic**:

  > "Does this combo + sizing pass the +10% target *fast*, AND does the
  >  account survive X months without a breach with >= 95-99% probability?"

This module answers it by **block-bootstrapping the real per-trade ledger**
(P&L + entry/exit timestamps, from ``run_system_backtest(attach_full=True)``
→ ``closed_trades``) into N synthetic trade sequences, walking each as a fresh
$account_size account compounded at a chosen ``risk_pct``, and aggregating:

  * ``P(pass)``          — fraction of paths that reach the +profit_target.
  * trades/days-to-pass  — {median, p5, p95} over the paths that passed.
  * ``P(survive Nmo)``   — fraction with NO account-killing breach within the
                           N-month horizon (static-DD off start, or daily-loss).
  * ``P(breach)``        — split by cause (static_drawdown / daily_loss).
  * end-return           — mean / median fraction over all paths.

Sizing-independence
-------------------
The ledger's ``pnl`` was sized at the BACKTEST's ``risk_pct`` against a
*compounding* balance, so we cannot replay it verbatim at a different risk.
We convert each trade to a **risk-multiple (R)** that is independent of both
the sizing and the running balance::

    R_k = pnl_k / (balance_before_k * base_risk_pct / 100)

``balance_before_k`` is recoverable because the portfolio engine holds **one
shared position at a time** (``reentry_policy="suppress"``): trades close in
entry order, so the balance at trade k's entry is just
``initial + sum(pnl of trades 0..k-1)``. In the walk we then re-realise each
trade as ``pnl = R_k * (running_balance * target_risk_pct / 100)`` — i.e. risk
a fixed fraction of the *live* balance per trade, compounding exactly as a real
account would.

Block bootstrap
---------------
Trades are resampled in contiguous **timestamped blocks** (default 8 trades)
rather than i.i.d. singletons, so autocorrelation (win/loss streaks, regime
runs) is preserved. Each block carries its **inter-trade time gaps**, so a
synthetic calendar exists: a path's elapsed time is the sum of the per-trade
gaps, and trades are bucketed onto synthetic UTC days for the daily-loss check
and the month-horizon survival check.

Honesty / limits (documented, not hidden)
-----------------------------------------
* **Daily-loss is REALISED-only.** A per-trade bootstrap has no intraday
  open-position equity swing, so a day's loss is the sum of that day's closed
  trade P&L. Breakout's real rule fires on intraday *equity* (mark-to-market
  including open positions). Our estimate is therefore **optimistic** for the
  daily-loss rule — it under-counts daily-loss breaches. Static-DD is checked
  on the realised balance after each trade (also realised-only, same caveat).
* **Backtest != funded reality** — slippage, funding, fills, and Breakout's
  exact equity accounting differ. This ranks *relative* robustness and
  estimates *probabilities*; it is a filter, not a guarantee.
* The synthetic calendar reuses the historical gap distribution; it does not
  model that BTC volatility clusters in calendar time beyond what the blocks
  capture.

Pure + deterministic given a seed. No network, no live-path imports.
Tier-1 research tooling.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from src.prop.evaluator import _to_dt  # tz-aware UTC coercion (re-used)
from src.prop.ruleset import PropRuleset

_SECONDS_PER_DAY = 86_400.0
# Approximate month length for the survival horizons (calendar months vary; the
# bootstrap calendar is synthetic, so a fixed 30-day month is the honest unit).
_DAYS_PER_MONTH = 30.0


@dataclass
class LedgerTrade:
    """One closed trade reduced to what the Monte-Carlo walk needs.

    ``r_multiple`` is the sizing-independent outcome (pnl / risk_usd_at_entry);
    ``gap_seconds`` is the wall-clock gap from the PREVIOUS trade's exit to this
    trade's exit (so summing gaps reconstructs the synthetic calendar).
    """

    r_multiple: float
    gap_seconds: float
    entry_ts: Any = None
    exit_ts: Any = None


# ---------------------------------------------------------------------------
# Ledger -> sizing-independent R sequence
# ---------------------------------------------------------------------------
def ledger_to_r_sequence(
    closed_trades: Sequence[Any],
    *,
    initial_balance: float,
    base_risk_pct: float,
) -> List[LedgerTrade]:
    """Convert the engine's closed-trade ledger into a list of :class:`LedgerTrade`.

    Each trade's ``r_multiple`` is ``pnl / (balance_before * base_risk_pct/100)``
    — independent of sizing and running balance (see module docstring). The
    ledger is processed in **exit-time order** (single shared position ⇒ exit
    order == entry order), replaying the balance so ``balance_before`` is exact.

    ``closed_trades`` items may be ``_ClosedTrade`` dataclasses (attrs) or plain
    dicts; both ``pnl`` and the timestamps are read by best-effort accessor.
    """
    if base_risk_pct <= 0:
        raise ValueError("base_risk_pct must be > 0")

    def _get(t: Any, key: str, default: Any = None) -> Any:
        if isinstance(t, dict):
            return t.get(key, default)
        return getattr(t, key, default)

    # Sort by exit timestamp (the close order the engine actually applied).
    def _exit_key(t: Any):
        ex = _get(t, "exit_ts")
        try:
            return _to_dt(ex)
        except Exception:  # noqa: BLE001 — unparsable ts sinks to the end stably
            return datetime.max.replace(tzinfo=timezone.utc)

    ordered = sorted(closed_trades, key=_exit_key)

    out: List[LedgerTrade] = []
    balance = float(initial_balance)
    prev_exit_dt: Optional[datetime] = None
    risk_frac = base_risk_pct / 100.0
    for t in ordered:
        pnl = float(_get(t, "pnl", 0.0) or 0.0)
        risk_usd = balance * risk_frac
        # A non-positive balance (shouldn't happen pre-breach) or zero risk_usd
        # would make R undefined; guard so one bad row can't poison the sequence.
        r = (pnl / risk_usd) if risk_usd > 0 else 0.0

        exit_ts = _get(t, "exit_ts")
        try:
            exit_dt = _to_dt(exit_ts)
        except Exception:  # noqa: BLE001
            exit_dt = prev_exit_dt or datetime(2023, 1, 1, tzinfo=timezone.utc)
        if prev_exit_dt is None:
            gap = 0.0
        else:
            gap = max(0.0, (exit_dt - prev_exit_dt).total_seconds())
        prev_exit_dt = exit_dt

        out.append(LedgerTrade(r_multiple=r, gap_seconds=gap,
                               entry_ts=_get(t, "entry_ts"), exit_ts=exit_ts))
        balance += pnl
    return out


# ---------------------------------------------------------------------------
# Block bootstrap
# ---------------------------------------------------------------------------
def _bootstrap_indices(
    n: int,
    target_len: int,
    block_len: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """A circular block-bootstrap index array of length >= target_len.

    Picks random block start positions and lays down contiguous blocks of
    ``block_len`` (wrapping circularly so late-history trades aren't
    under-sampled), until at least ``target_len`` indices are produced; then
    truncates. Preserves local trade-to-trade autocorrelation.
    """
    if n <= 0:
        return np.empty(0, dtype=int)
    block_len = max(1, min(block_len, n))
    idx: List[int] = []
    while len(idx) < target_len:
        start = int(rng.integers(0, n))
        for j in range(block_len):
            idx.append((start + j) % n)
    return np.asarray(idx[:target_len], dtype=int)


@dataclass
class PathResult:
    passed: bool
    trades_to_pass: Optional[int]
    days_to_pass: Optional[float]
    breached: bool
    breach_cause: Optional[str]
    breach_day: Optional[float]   # synthetic day index of the breach
    end_return: float             # final balance / initial - 1
    survived_horizon: Dict[float, bool]  # {months: survived (no breach by then)}


def _simulate_path(
    r_seq: Sequence[LedgerTrade],
    idx: np.ndarray,
    *,
    account_size: float,
    risk_pct: float,
    target_pct: float,
    daily_loss_pct: Optional[float],
    static_dd_pct: Optional[float],
    horizons_months: Sequence[float],
) -> PathResult:
    """Walk one synthetic path; return its pass/breach/horizon outcome.

    The walk records the FIRST of (pass, breach) by trade order but continues
    to the horizon end so that ``survived_horizon`` reflects whether ANY breach
    occurred within each month-window (a pass does not stop breach-watching —
    Breakout's limits stay in force in the funded phase).
    """
    risk_frac = risk_pct / 100.0
    balance = float(account_size)
    target_balance = account_size * (1.0 + target_pct)
    static_floor = (
        account_size * (1.0 - static_dd_pct) if static_dd_pct is not None else None
    )

    elapsed_days = 0.0
    cur_day = 0                 # synthetic UTC-day bucket (floor of elapsed_days)
    day_start_balance = balance

    passed = False
    trades_to_pass: Optional[int] = None
    days_to_pass: Optional[float] = None

    breached = False
    breach_cause: Optional[str] = None
    breach_day: Optional[float] = None

    horizon_days = sorted({float(m) * _DAYS_PER_MONTH for m in horizons_months})
    survived = {hd: True for hd in horizon_days}

    n_seq = len(r_seq)
    for k, ix in enumerate(idx):
        trade = r_seq[int(ix) % n_seq] if n_seq else None
        if trade is None:
            break

        # advance the synthetic clock by this trade's inter-trade gap
        elapsed_days += trade.gap_seconds / _SECONDS_PER_DAY
        new_day = int(elapsed_days)
        if new_day != cur_day:
            cur_day = new_day
            day_start_balance = balance

        # realise the trade at the live balance + target risk
        pnl = trade.r_multiple * (balance * risk_frac)
        balance += pnl

        # ---- breach checks (realised-only; see module docstring caveat) ----
        if not breached:
            # static drawdown off the STARTING balance
            if static_floor is not None and balance <= static_floor + 1e-9:
                breached = True
                breach_cause = "static_drawdown"
                breach_day = elapsed_days
            # daily-loss: this day's realised drop from day-start balance
            elif (
                daily_loss_pct is not None
                and day_start_balance > 0
                and (day_start_balance - balance) / day_start_balance
                > daily_loss_pct + 1e-12
            ):
                breached = True
                breach_cause = "daily_loss"
                breach_day = elapsed_days
            # mark every horizon whose window has already been crossed by a breach
            if breached:
                for hd in horizon_days:
                    if breach_day is not None and breach_day <= hd:
                        survived[hd] = False

        # ---- target check (first crossing) ----
        if not passed and balance >= target_balance:
            passed = True
            trades_to_pass = k + 1
            days_to_pass = elapsed_days

        # once breached, the account is dead — stop walking
        if breached:
            break

    survived_by_month = {
        float(m): survived[float(m) * _DAYS_PER_MONTH] for m in horizons_months
    }
    return PathResult(
        passed=passed,
        trades_to_pass=trades_to_pass,
        days_to_pass=days_to_pass,
        breached=breached,
        breach_cause=breach_cause,
        breach_day=breach_day,
        end_return=(balance / account_size - 1.0) if account_size else 0.0,
        survived_horizon=survived_by_month,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def _pctile(vals: Sequence[float], q: float) -> Optional[float]:
    if not vals:
        return None
    return float(np.percentile(np.asarray(vals, dtype=float), q))


def run_montecarlo(
    closed_trades: Sequence[Any],
    ruleset: PropRuleset,
    *,
    risk_pct: float,
    base_risk_pct: float,
    account_size: Optional[float] = None,
    n_paths: int = 5000,
    block_len: int = 8,
    horizons_months: Sequence[float] = (3.0, 6.0, 12.0),
    seed: int = 1234,
    path_trades: Optional[int] = None,
) -> Dict[str, Any]:
    """Run the block-bootstrap survival+speed Monte-Carlo for one combo+sizing.

    Parameters
    ----------
    closed_trades : the engine's ``closed_trades`` ledger (``attach_full=True``).
    ruleset       : the parsed prop ruleset (account size, target, limits).
    risk_pct      : the per-trade risk %% to SIMULATE (the sizing under test).
    base_risk_pct : the ``risk_pct`` the ``closed_trades`` ledger was generated
                    at (needed to back out the sizing-independent R sequence).
    n_paths       : number of synthetic paths (default 5000).
    block_len     : bootstrap block length in trades (default 8).
    horizons_months : survival horizons to report (default 3/6/12 months).
    seed          : RNG seed (fixed → reproducible).
    path_trades   : trades per synthetic path. Default: enough blocks to cover
                    the longest horizon at the historical trade cadence, capped
                    so a degenerate (near-zero-gap) ledger can't explode it.

    Returns a JSON-serializable aggregate dict (see module docstring).
    """
    acct = float(account_size) if account_size is not None else float(ruleset.account_size_usd)
    target_pct = ruleset.evaluation.profit_target_pct or 0.0
    daily_loss_pct = ruleset.limits.daily_loss_pct
    static_dd_pct = (
        ruleset.limits.max_drawdown_pct
        if ruleset.limits.drawdown_type == "static"
        else None
    )

    r_seq = ledger_to_r_sequence(
        closed_trades, initial_balance=acct, base_risk_pct=base_risk_pct
    )
    n_trades = len(r_seq)

    if n_trades == 0:
        return {
            "risk_pct": risk_pct,
            "n_paths": n_paths,
            "n_ledger_trades": 0,
            "error": "empty_ledger",
            "p_pass": 0.0,
            "p_breach": 0.0,
            "breach_by_cause": {},
            "trades_to_pass": {}, "days_to_pass": {},
            "survival": {str(m): None for m in horizons_months},
            "end_return": {},
        }

    # How many trades per path: cover the longest horizon at the median cadence,
    # with a floor and a hard ceiling so a tiny/odd ledger stays bounded.
    if path_trades is None:
        max_h_days = max(float(m) * _DAYS_PER_MONTH for m in horizons_months)
        gaps = [t.gap_seconds for t in r_seq if t.gap_seconds > 0]
        med_gap_days = (median(gaps) / _SECONDS_PER_DAY) if gaps else 1.0
        if med_gap_days <= 0:
            med_gap_days = 1.0
        # +25% headroom so paths actually reach the far horizon
        need = int((max_h_days / med_gap_days) * 1.25) + block_len
        path_trades = max(50, min(need, 20_000))

    rng = np.random.default_rng(seed)

    paths: List[PathResult] = []
    for _ in range(n_paths):
        idx = _bootstrap_indices(n_trades, path_trades, block_len, rng)
        paths.append(
            _simulate_path(
                r_seq, idx,
                account_size=acct, risk_pct=risk_pct, target_pct=target_pct,
                daily_loss_pct=daily_loss_pct, static_dd_pct=static_dd_pct,
                horizons_months=horizons_months,
            )
        )

    n = len(paths)
    n_pass = sum(1 for p in paths if p.passed)
    n_breach = sum(1 for p in paths if p.breached)
    cause_counts: Dict[str, int] = {}
    for p in paths:
        if p.breached and p.breach_cause:
            cause_counts[p.breach_cause] = cause_counts.get(p.breach_cause, 0) + 1

    pass_trades = [p.trades_to_pass for p in paths if p.passed and p.trades_to_pass is not None]
    pass_days = [p.days_to_pass for p in paths if p.passed and p.days_to_pass is not None]
    end_returns = [p.end_return for p in paths]

    survival: Dict[str, Optional[float]] = {}
    for m in horizons_months:
        survived = sum(1 for p in paths if p.survived_horizon.get(float(m), True))
        survival[str(m)] = round(survived / n, 4) if n else None

    return {
        "risk_pct": risk_pct,
        "base_risk_pct": base_risk_pct,
        "n_paths": n,
        "n_ledger_trades": n_trades,
        "path_trades": path_trades,
        "block_len": block_len,
        "account_size": acct,
        "p_pass": round(n_pass / n, 4) if n else 0.0,
        "p_breach": round(n_breach / n, 4) if n else 0.0,
        "breach_by_cause": {
            k: round(v / n, 4) for k, v in sorted(cause_counts.items())
        },
        "trades_to_pass": {
            "median": (round(median(pass_trades), 1) if pass_trades else None),
            "p5": (round(_pctile(pass_trades, 5), 1) if pass_trades else None),
            "p95": (round(_pctile(pass_trades, 95), 1) if pass_trades else None),
        },
        "days_to_pass": {
            "median": (round(median(pass_days), 1) if pass_days else None),
            "p5": (round(_pctile(pass_days, 5), 1) if pass_days else None),
            "p95": (round(_pctile(pass_days, 95), 1) if pass_days else None),
        },
        "survival": survival,
        "end_return": {
            "mean": round(float(np.mean(end_returns)), 4) if end_returns else None,
            "median": round(float(median(end_returns)), 4) if end_returns else None,
        },
    }


# ---------------------------------------------------------------------------
# Cost-aware EV (expected $ netted per horizon, net of fees, re-buying on breach)
# ---------------------------------------------------------------------------
@dataclass
class EvPathResult:
    """One EV path: realised payouts net of fees, snapshotted per horizon.

    ``net_by_h`` maps a horizon (months) → (banked × profit_split − fees) at
    that point in synthetic time. ``banked`` is the gross trader-share withdrawn
    over the whole walk; ``fees`` the total spent on the first account + every
    re-buy after a breach.
    """

    net_by_h: Dict[float, float]
    banked: float
    fees: float
    n_accounts: int
    n_pass: int
    n_breach: int


def _simulate_ev_path(
    r_seq: Sequence[LedgerTrade],
    idx: np.ndarray,
    *,
    account_size: float,
    risk_pct: float,
    target_pct: float,
    daily_loss_pct: Optional[float],
    static_dd_pct: Optional[float],
    horizons_months: Sequence[float],
    account_fee: float,
    rebuy_fee: float,
    profit_split: float,
    first_payout_after_days: float,
    payout_frequency_days: float,
    min_withdrawal_usd: float,
    buffer_usd: float,
) -> EvPathResult:
    """Walk one path as a RENEWABLE account: buy → (pass → bank-ASAP) → breach
    → re-buy, over the longest horizon, snapshotting net-$ at each horizon.

    The synthetic clock advances per-trade exactly as in :func:`_simulate_path`,
    so re-buys consume the same calendar; an account that breaches fast simply
    yields more accounts (more fees) in the same window. Withdrawals bank
    ``profit_split`` of all equity above ``account_size + buffer_usd`` at every
    allowed payout window (default: weekly from day ``first_payout_after_days``
    post-funding) and reset the in-account balance to that floor — so banked cash
    is safe but the static-DD cushion is never widened by retained profit.
    """
    risk_frac = risk_pct / 100.0
    target_balance = account_size * (1.0 + target_pct)
    static_floor = (
        account_size * (1.0 - static_dd_pct) if static_dd_pct is not None else None
    )
    withdraw_above = account_size + max(0.0, buffer_usd)
    horizon_days = sorted({float(m) * _DAYS_PER_MONTH for m in horizons_months})
    max_h = horizon_days[-1] if horizon_days else 0.0

    banked = 0.0
    fees = 0.0
    n_accounts = 0
    n_pass = 0
    n_breach = 0

    # per-account state
    balance = account_size
    passed = False
    next_payout_day: Optional[float] = None
    day_start_balance = account_size
    cur_day = 0

    def _open_account(is_first: bool) -> None:
        nonlocal balance, passed, next_payout_day, day_start_balance, cur_day, fees, n_accounts
        balance = account_size
        passed = False
        next_payout_day = None
        day_start_balance = account_size
        cur_day = int(elapsed_days)
        fees += account_fee if is_first else rebuy_fee
        n_accounts += 1

    elapsed_days = 0.0
    _open_account(True)

    snaps: Dict[float, float] = {}
    hi = 0
    n_seq = len(r_seq)

    for k, ix in enumerate(idx):
        if elapsed_days >= max_h:
            break
        trade = r_seq[int(ix) % n_seq] if n_seq else None
        if trade is None:
            break

        elapsed_days += trade.gap_seconds / _SECONDS_PER_DAY
        # snapshot net for any horizon we've now crossed
        while hi < len(horizon_days) and elapsed_days >= horizon_days[hi]:
            snaps[horizon_days[hi]] = banked - fees
            hi += 1
        if elapsed_days >= max_h:
            break

        new_day = int(elapsed_days)
        if new_day != cur_day:
            cur_day = new_day
            day_start_balance = balance

        balance += trade.r_multiple * (balance * risk_frac)

        if not passed and balance >= target_balance:
            passed = True
            n_pass += 1
            next_payout_day = elapsed_days + first_payout_after_days

        # bank-ASAP withdrawal at each allowed window
        if passed and next_payout_day is not None and elapsed_days >= next_payout_day:
            withdrawable = balance - withdraw_above
            if withdrawable >= max(0.0, min_withdrawal_usd):
                banked += withdrawable * profit_split
                balance -= withdrawable          # bank everything above the floor
                day_start_balance = balance       # a withdrawal is not a trading loss
            # advance to the next window (skip any windows the gap jumped over)
            step = payout_frequency_days if payout_frequency_days > 0 else 7.0
            while next_payout_day <= elapsed_days:
                next_payout_day += step

        breached = False
        if static_floor is not None and balance <= static_floor + 1e-9:
            breached = True
        elif (
            daily_loss_pct is not None
            and day_start_balance > 0
            and (day_start_balance - balance) / day_start_balance > daily_loss_pct + 1e-12
        ):
            breached = True

        if breached:
            n_breach += 1
            if elapsed_days < max_h:
                _open_account(False)   # re-buy and keep trading the remaining time

    while hi < len(horizon_days):
        snaps[horizon_days[hi]] = banked - fees
        hi += 1

    net_by_h = {float(m): snaps.get(float(m) * _DAYS_PER_MONTH, banked - fees)
                for m in horizons_months}
    return EvPathResult(
        net_by_h=net_by_h, banked=banked, fees=fees,
        n_accounts=n_accounts, n_pass=n_pass, n_breach=n_breach,
    )


def run_ev_montecarlo(
    closed_trades: Sequence[Any],
    ruleset: PropRuleset,
    *,
    risk_pct: float,
    base_risk_pct: float,
    account_size: Optional[float] = None,
    n_paths: int = 5000,
    block_len: int = 8,
    horizons_months: Sequence[float] = (3.0, 6.0, 12.0),
    seed: int = 1234,
    path_trades: Optional[int] = None,
) -> Dict[str, Any]:
    """Cost-aware EV sweep for one combo+sizing.

    Reuses the same block-bootstrap of the real ledger as :func:`run_montecarlo`,
    but each path is walked as a **renewable account** (buy → pass → bank-ASAP →
    breach → re-buy) so the headline is **expected dollars netted per horizon,
    net of fees** — the metric that credits a strategy which burns an account
    fast but banks more than its fee first. Returns, per horizon: mean / median /
    p5 / p95 net-$, P(net > 0), and mean accounts-burned / fees / ROI-on-fees.

    Economics (fees, payout cadence, withdrawal policy) come from
    ``ruleset.economics``; ``profit_split`` from ``ruleset.profit_split``.
    """
    acct = float(account_size) if account_size is not None else float(ruleset.account_size_usd)
    target_pct = ruleset.evaluation.profit_target_pct or 0.0
    daily_loss_pct = ruleset.limits.daily_loss_pct
    static_dd_pct = (
        ruleset.limits.max_drawdown_pct if ruleset.limits.drawdown_type == "static" else None
    )
    econ = ruleset.economics

    r_seq = ledger_to_r_sequence(closed_trades, initial_balance=acct, base_risk_pct=base_risk_pct)
    n_trades = len(r_seq)
    base = {
        "risk_pct": risk_pct, "base_risk_pct": base_risk_pct, "n_paths": n_paths,
        "n_ledger_trades": n_trades, "account_size": acct,
        "profit_split": ruleset.profit_split,
        "account_fee_usd": econ.account_fee_usd, "rebuy_fee_usd": econ.rebuy_fee_usd,
    }
    if n_trades == 0:
        return {**base, "error": "empty_ledger", "horizons": {}}

    if path_trades is None:
        max_h_days = max(float(m) * _DAYS_PER_MONTH for m in horizons_months)
        gaps = [t.gap_seconds for t in r_seq if t.gap_seconds > 0]
        med_gap_days = (median(gaps) / _SECONDS_PER_DAY) if gaps else 1.0
        if med_gap_days <= 0:
            med_gap_days = 1.0
        need = int((max_h_days / med_gap_days) * 1.5) + block_len
        path_trades = max(50, min(need, 40_000))

    rng = np.random.default_rng(seed)
    results: List[EvPathResult] = []
    for _ in range(n_paths):
        idx = _bootstrap_indices(n_trades, path_trades, block_len, rng)
        results.append(_simulate_ev_path(
            r_seq, idx,
            account_size=acct, risk_pct=risk_pct, target_pct=target_pct,
            daily_loss_pct=daily_loss_pct, static_dd_pct=static_dd_pct,
            horizons_months=horizons_months,
            account_fee=econ.account_fee_usd, rebuy_fee=econ.rebuy_fee_usd,
            profit_split=ruleset.profit_split,
            first_payout_after_days=econ.payout.first_payout_after_days,
            payout_frequency_days=econ.payout.payout_frequency_days,
            min_withdrawal_usd=econ.payout.min_withdrawal_usd,
            buffer_usd=econ.withdrawal_policy.buffer_usd,
        ))

    n = len(results)
    horizons_out: Dict[str, Any] = {}
    for m in horizons_months:
        nets = [r.net_by_h.get(float(m), 0.0) for r in results]
        fees = [r.fees for r in results]
        accts = [r.n_accounts for r in results]
        mean_net = float(np.mean(nets)) if nets else 0.0
        mean_fees = float(np.mean(fees)) if fees else 0.0
        horizons_out[str(float(m))] = {
            "mean_net_usd": round(mean_net, 2),
            "median_net_usd": round(float(median(nets)), 2) if nets else None,
            "p5_net_usd": round(_pctile(nets, 5), 2) if nets else None,
            "p95_net_usd": round(_pctile(nets, 95), 2) if nets else None,
            "p_profitable": round(sum(1 for x in nets if x > 0) / n, 4) if n else 0.0,
            "mean_accounts": round(float(np.mean(accts)), 2) if accts else None,
            "mean_fees_usd": round(mean_fees, 2),
            "roi_on_fees": round(mean_net / mean_fees, 3) if mean_fees > 0 else None,
        }

    return {**base, "path_trades": path_trades, "block_len": block_len, "horizons": horizons_out}
