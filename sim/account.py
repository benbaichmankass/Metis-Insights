"""SIM Phase-5 account-realism layer (OPTIONAL, additive, back-compatible).

Folds the **$ account model** that today lives only in
``scripts/backtest_system.py`` into the integrated ``sim/`` harness, so the
canonical harness can also answer "what would the *capital* have done?" — final
balance, net $, return %, max-DD $/%, return/DD, capital utilization, and
per-strategy $ attribution — on top of the R-based replay it already produces.

Cardinal rules honored (``docs/sprint-plans/ROADMAP-INTEGRATED-SIM-2026-05-30.md``
§ 4):
  * **Reuse, don't fork.** The sizing/halt/$-summary math mirrors
    ``backtest_system`` 1-for-1 (``_risk_qty``: ``bal*(risk_pct/100)/stop_dist``;
    daily-loss halt as a *percent* of the day-start balance). We do NOT
    reimplement signal logic, intent resolution, or the fill model — the engine
    still drives those through the live ``aggregate_intents`` + the real
    ``BarFillModel``.
  * **Determinism (§ 4 rule 4).** No clock, no RNG: $ output is a pure function
    of (R-multiples, sizing inputs, bar timestamps).
  * **Opt-in.** When ``run_replay`` is called without an ``AccountConfig`` the
    engine and ledger behave EXACTLY as before — no ``account`` block is emitted
    and the R metrics are byte-identical (Phase 1–4's 49 tests stay green).

Note on units (matches ``backtest_system``): ``risk_pct`` and ``daily_loss_pct``
are **percentages** (``1.0`` = 1%, not 0.01).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AccountConfig:
    """Knobs for the optional $ account layer (percent units, like backtest_system).

    initial_balance : starting equity in account currency.
    risk_pct        : percent of *current* balance risked per trade (1.0 = 1%).
    daily_loss_pct  : percent of the UTC day's start balance that, once lost
                      (realized) in that day, halts new opens for the rest of the
                      day. ``0.0`` disables the halt.
    """

    initial_balance: float = 10_000.0
    risk_pct: float = 1.0
    daily_loss_pct: float = 0.0


def _utc_day(ts: Any) -> str:
    """Map a bar ts (epoch seconds or ISO/date string) to a UTC ``YYYY-MM-DD``.

    Deterministic + tz-safe: epoch numbers are UTC seconds; strings are parsed
    with pandas (same coercion the rest of sim uses) and floored to the day.
    Falls back to ``"?"`` so a missing/garbage ts never crashes the halt
    bookkeeping (mirrors backtest_system's per-bar ``ts.date()`` grouping).
    """
    if ts is None:
        return "?"
    import datetime as _dt

    if isinstance(ts, (int, float)):
        return _dt.datetime.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d")
    try:
        s = str(ts).replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(s).strftime("%Y-%m-%d")
    except ValueError:
        # last resort: take the leading date token (e.g. "2026-01-01 00:00:00")
        token = str(ts).strip().split("T")[0].split(" ")[0]
        return token or "?"


@dataclass
class SimAccount:
    """Tracks $ equity alongside the R-based replay.

    The engine calls, per bar: ``mark_bar`` (capital-utilization + day-start
    snapshot), ``can_open`` (daily-loss halt gate) before an entry, ``size`` to
    compute the 1R risk-cash committed at the fill, and ``on_close`` when the
    fill model realizes a trade (turns the realized R into $).
    """

    config: AccountConfig
    balance: float = field(init=False)
    _day_pnl: dict = field(default_factory=dict, init=False)
    _day_start_balance: dict = field(default_factory=dict, init=False)
    _halted_days: set = field(default_factory=set, init=False)
    _strat_pnl: dict = field(default_factory=dict, init=False)
    _strat_trades: dict = field(default_factory=dict, init=False)
    _peak_balance: float = field(init=False)
    _max_dd_usd: float = field(default=0.0, init=False)
    _max_dd_pct: float = field(default=0.0, init=False)
    _bars_with_open: int = field(default=0, init=False)
    _total_bars: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.balance = float(self.config.initial_balance)
        self._peak_balance = self.balance

    # -- sizing (ported from backtest_system._risk_qty) --------------------- #
    def risk_qty(self, entry: float, sl: float) -> float:
        """Quantity such that a stop-out loses exactly ``risk_pct`` of balance.

        ``qty = (balance * risk_pct/100) / |entry - sl|`` — identical to
        ``backtest_system._risk_qty``. Returns 0.0 on a degenerate stop
        distance / balance / risk (the open is then skipped, as live).
        """
        stop_dist = abs(entry - sl)
        if stop_dist <= 0 or self.balance <= 0 or self.config.risk_pct <= 0:
            return 0.0
        return (self.balance * (self.config.risk_pct / 100.0)) / stop_dist

    def size(self, entry: float, sl: float) -> float:
        """Risk-cash (1R, in $) committed for a position sized at ``entry``/``sl``.

        Returns ``balance * risk_pct/100`` when the stop distance is valid, else
        0.0 (engine treats 0.0 as "skip the open"). $ PnL on close = R * this.
        """
        if self.risk_qty(entry, sl) <= 0:
            return 0.0
        return self.balance * (self.config.risk_pct / 100.0)

    # -- per-bar bookkeeping ------------------------------------------------ #
    def note_day(self, ts: Any) -> None:
        """Snapshot the UTC day's *start* balance on its first bar.

        Must be called once per bar DURING the replay (before any open), so the
        daily-loss cap is measured against the balance at the day's open — exactly
        like backtest_system's ``day_start_balance`` reset on a date change.
        """
        day = _utc_day(ts)
        if day not in self._day_start_balance:
            self._day_start_balance[day] = self.balance

    def mark_utilization(self, total_bars: int, bars_with_open: int) -> None:
        """Record capital-utilization counts (bars-deployed / total)."""
        self._total_bars = total_bars
        self._bars_with_open = bars_with_open

    # -- entry gate (daily-loss halt) -------------------------------------- #
    def can_open(self, ts: Any) -> bool:
        """False when the UTC day's realized loss has breached the cap.

        Mirrors backtest_system:
          ``(balance - day_start) <= -abs(daily_loss_pct)/100 * day_start``
        evaluated on the day's realized PnL. ``daily_loss_pct == 0`` disables it.
        """
        if self.config.daily_loss_pct <= 0:
            return True
        day = _utc_day(ts)
        start_bal = self._day_start_balance.get(day, self.balance)
        cap = -abs(self.config.daily_loss_pct) / 100.0 * start_bal
        if self._day_pnl.get(day, 0.0) <= cap:
            self._halted_days.add(day)
            return False
        return True

    # -- exit handling ------------------------------------------------------ #
    def on_close(self, strategy: str, risk_cash: float, r_multiple: float,
                 ts: Any) -> None:
        """Realize a closed trade: $ PnL = R * risk_cash committed at entry."""
        pnl = float(r_multiple) * float(risk_cash)
        self.balance += pnl
        day = _utc_day(ts)
        self._day_pnl[day] = self._day_pnl.get(day, 0.0) + pnl
        self._strat_pnl[strategy] = self._strat_pnl.get(strategy, 0.0) + pnl
        self._strat_trades[strategy] = self._strat_trades.get(strategy, 0) + 1
        if self.balance > self._peak_balance:
            self._peak_balance = self.balance
        dd = self._peak_balance - self.balance
        if dd > self._max_dd_usd:
            self._max_dd_usd = dd
            self._max_dd_pct = (dd / self._peak_balance * 100.0) if self._peak_balance else 0.0

    # -- $ summary (ported from backtest_system._summarize) ----------------- #
    def summary(self) -> dict[str, Any]:
        """$ account summary after a full replay (mirrors backtest_system)."""
        init = self.config.initial_balance
        net = self.balance - init
        per_strategy = {
            sid: {"pnl_usd": round(v, 2), "trades": self._strat_trades.get(sid, 0)}
            for sid, v in self._strat_pnl.items()
        }
        return {
            "initial_balance": round(init, 2),
            "final_balance": round(self.balance, 2),
            "net_usd": round(net, 2),
            "return_pct": round(100.0 * net / init, 2) if init else 0.0,
            "max_drawdown_usd": round(self._max_dd_usd, 2),
            "max_drawdown_pct": round(self._max_dd_pct, 2),
            "return_over_dd": round(net / self._max_dd_usd, 2) if self._max_dd_usd > 0 else None,
            "capital_utilization_pct": (
                round(100.0 * self._bars_with_open / self._total_bars, 2)
                if self._total_bars else 0.0
            ),
            "per_strategy_usd": per_strategy,
            "halted_days": sorted(self._halted_days),
        }
