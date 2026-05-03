"""Prop-account risk manager — mission-aware extension of RiskManager.

Velotrade integration sprint (2026-05-03). Subclass of
:class:`src.units.accounts.risk.RiskManager` that adds three skip
reasons on top of the base daily-loss / position-size / drawdown gates:

  - ``SKIP_MISSION_MET`` — evaluation phase, profit target AND
    min-active-days both met. No upside in adding risk; refuse.
  - ``SKIP_OVERNIGHT_RESTRICTED`` — current UTC hour falls inside the
    account's overnight window. Prop firms typically forbid carrying
    positions through the close.
  - ``SKIP_WEEKEND_RESTRICTED`` — Sat/Sun UTC. Same rationale.

Account state machine (config-driven, no live state writes in v1):
  ``account_state == 'evaluation'``:
    - if profit_target_met AND min_active_days_met → SKIP (mission done).
    - else → allow (subject to base risk gates).
  ``account_state == 'funded'``:
    - identical to base RiskManager (subject to overnight / weekend
      restrictions if ``overnight_restricted`` is True).

State inputs are seeded from the YAML ``prop_state:`` block on each
``load_accounts()`` call. Persistence (write-through to a runtime
state file on every ``record_trade_result``) is deferred to a follow-up
PR — current contract is "operator updates the YAML between sessions".
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Dict, Optional

from src.core.coordinator import OrderPackage
from src.units.accounts.risk import RiskManager, _is_test_order


# Default overnight restriction window (UTC). Prop firms commonly forbid
# new entries from 22:00 UTC through 06:00 UTC the next day. Override
# per-account via ``overnight_window: [start_hour, end_hour]`` in YAML.
_DEFAULT_OVERNIGHT_START_HOUR = 22
_DEFAULT_OVERNIGHT_END_HOUR = 6


class PropRiskManager(RiskManager):
    """Mission-aware risk gate for prop firm evaluation + funded accounts.

    Parameters (added to the base RiskManager config dict)
    -------------------------------------------------------
    account_state : str
        ``"evaluation"`` (default) or ``"funded"``. Drives the mission
        check.
    phase_requirements : dict, optional
        ``target_profit_pct``: cumulative PnL fraction required to
        clear the evaluation (e.g. ``0.05`` for +5 %).
        ``min_active_days``: minimum number of days the account must
        have traded.
        ``min_daily_profit_pct``: informational; not gated here in v1.
    prop_state : dict, optional
        Live counters seeded from YAML. ``cumulative_pnl_pct``,
        ``active_days``. Updated in-process via
        :meth:`record_trade_result`; persistence deferred.
    overnight_restricted : bool
        When True, block new entries during the overnight window
        and on weekends (UTC).
    overnight_window : [int, int], optional
        ``[start_hour, end_hour]`` in UTC, both 0..23 inclusive.
        Defaults to ``[22, 6]``. The window wraps midnight when
        start > end.
    weekend_restricted : bool, optional
        Defaults to the value of ``overnight_restricted``. Set to
        False to allow Sat/Sun trading on a 24/7 prop product.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config.get("risk") or config)
        self.account_state: str = str(config.get("account_state") or "evaluation").lower()
        phase = config.get("phase_requirements") or {}
        self.target_profit_pct: float = float(phase.get("target_profit_pct", 0.05))
        self.min_active_days: int = int(phase.get("min_active_days", 4))
        self.min_daily_profit_pct: float = float(phase.get("min_daily_profit_pct", 0.0))

        prop_state = config.get("prop_state") or {}
        self.cumulative_pnl_pct: float = float(prop_state.get("cumulative_pnl_pct") or 0.0)
        self.active_days: int = int(prop_state.get("active_days") or 0)
        self._entry_date_iso: Optional[str] = prop_state.get("entry_date") or None

        # Default to False so legacy prop fixtures (test_coordinator_flow,
        # test_s010_accounts) that pre-date the Velotrade integration
        # don't suddenly trip the overnight gate at certain UTC hours.
        # New prop accounts opt in by setting ``overnight_restricted:
        # true`` in their YAML (see prop_velotrade_1).
        self.overnight_restricted: bool = bool(config.get("overnight_restricted", False))
        window = config.get("overnight_window") or [
            _DEFAULT_OVERNIGHT_START_HOUR,
            _DEFAULT_OVERNIGHT_END_HOUR,
        ]
        self.overnight_start_hour: int = int(window[0])
        self.overnight_end_hour: int = int(window[1])
        self.weekend_restricted: bool = bool(
            config.get("weekend_restricted", self.overnight_restricted)
        )

    # ------------------------------------------------------------------
    # Mission predicates
    # ------------------------------------------------------------------

    def profit_target_met(self) -> bool:
        return self.cumulative_pnl_pct >= self.target_profit_pct

    def active_days_met(self) -> bool:
        return self.active_days >= self.min_active_days

    def mission_complete(self) -> bool:
        """Evaluation phase passed — no further trades needed."""
        return self.profit_target_met() and self.active_days_met()

    # ------------------------------------------------------------------
    # Time-window predicates
    # ------------------------------------------------------------------

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    def is_overnight_window(self, now: Optional[datetime] = None) -> bool:
        """True when *now* (UTC) falls inside ``[start_hour, end_hour)``.

        The window wraps midnight when ``start > end`` (e.g. 22 → 6).
        """
        if not self.overnight_restricted:
            return False
        ts = (now or self._now_utc())
        hour = ts.hour
        start = self.overnight_start_hour
        end = self.overnight_end_hour
        if start == end:
            return False
        if start < end:
            return start <= hour < end
        # wraps midnight
        return hour >= start or hour < end

    def is_weekend(self, now: Optional[datetime] = None) -> bool:
        if not self.weekend_restricted:
            return False
        ts = (now or self._now_utc())
        return ts.weekday() >= 5  # Sat=5, Sun=6

    # ------------------------------------------------------------------
    # Gate
    # ------------------------------------------------------------------

    def evaluate(
        self,
        order: OrderPackage,
        *,
        now: Optional[datetime] = None,
    ) -> tuple[bool, Optional[str]]:
        """Mission-aware gate.

        Order of checks:
          1. Smoke-test bypass (same as base).
          2. Weekend restriction (prop accounts only).
          3. Overnight restriction (prop accounts only).
          4. Mission-complete skip (evaluation only).
          5. Base RiskManager checks (daily loss / pos size / drawdown).
        """
        if _is_test_order(order):
            return True, None

        if self.is_weekend(now):
            return False, "SKIP_WEEKEND_RESTRICTED"

        if self.is_overnight_window(now):
            return False, "SKIP_OVERNIGHT_RESTRICTED"

        if self.account_state == "evaluation" and self.mission_complete():
            return False, "SKIP_MISSION_MET"

        return super().evaluate(order)

    # ------------------------------------------------------------------
    # State-update hooks (in-process; persistence deferred)
    # ------------------------------------------------------------------

    def record_trade_result(
        self,
        pnl_usd: float,
        *,
        starting_equity_usd: Optional[float] = None,
    ) -> None:
        """Update daily PnL + cumulative PnL fraction.

        ``starting_equity_usd`` is the equity at the start of the
        evaluation phase (used to convert USD pnl → percentage). When
        omitted we fall back to ``self.current_equity`` if seeded, or
        leave the cumulative fraction unchanged if neither is known.
        """
        super().record_trade_result(pnl_usd)

        seed = starting_equity_usd
        if seed is None:
            seed = self.current_equity
        if seed and seed > 0:
            self.cumulative_pnl_pct += float(pnl_usd) / float(seed)

        # Track active-day count: if today's UTC date isn't recorded
        # yet, count it.
        today_iso = datetime.now(timezone.utc).date().isoformat()
        if self._entry_date_iso != today_iso:
            self.active_days += 1
            self._entry_date_iso = today_iso

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def report(self) -> Dict[str, Any]:
        base = super().report()
        base.update({
            "account_state": self.account_state,
            "target_profit_pct": self.target_profit_pct,
            "min_active_days": self.min_active_days,
            "cumulative_pnl_pct": round(self.cumulative_pnl_pct, 6),
            "active_days": self.active_days,
            "profit_target_met": self.profit_target_met(),
            "active_days_met": self.active_days_met(),
            "mission_complete": self.mission_complete(),
            "overnight_restricted": self.overnight_restricted,
            "overnight_window": [self.overnight_start_hour, self.overnight_end_hour],
            "weekend_restricted": self.weekend_restricted,
            "in_overnight_window": self.is_overnight_window(),
            "is_weekend": self.is_weekend(),
        })
        return base
