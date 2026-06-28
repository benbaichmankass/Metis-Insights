"""Prop-account risk manager — mission-aware extension of RiskManager.

Prop-account integration sprint (2026-05-03). Subclass of
:class:`src.units.accounts.risk.RiskManager` that adds three skip
reasons on top of the base daily-loss / position-size / drawdown gates:

  - ``SKIP_MISSION_MET`` — evaluation phase, profit target AND
    min-active-days both met. No upside in adding risk; refuse.
  - ``SKIP_OVERNIGHT_RESTRICTED`` — current UTC hour falls inside the
    account's overnight window. Prop firms typically forbid carrying
    positions through the close.
  - ``SKIP_WEEKEND_RESTRICTED`` — Sat/Sun UTC. Same rationale.

Account state machine (config-driven; live state persists across
trader restarts — see "State persistence" below):
  ``account_state == 'evaluation'``:
    - if profit_target_met AND min_active_days_met → SKIP (mission done).
    - else → allow (subject to base risk gates).
  ``account_state == 'funded'``:
    - identical to base RiskManager (subject to overnight / weekend
      restrictions if ``overnight_restricted`` is True).

State persistence (two mechanisms, both live — the earlier "deferred to
a follow-up PR / operator updates the YAML between sessions" note is
obsolete):
  - **Mission counters** (``cumulative_pnl_pct`` / ``active_days`` /
    ``entry_date``) are seeded from the YAML ``prop_state:`` block on
    each ``load_accounts()`` call, then the live
    ``runtime_state/prop_state.json`` section (when present) overrides
    that seed so progress survives a restart. ``record_trade_result``
    writes the counters back atomically (see ``prop_state_io`` +
    ``_persist_state``).
  - **Base daily-loss / drawdown caps** persist separately via the
    journal-sourced ``daily_risk_state`` self-healing rebuild, which is
    active because ``__init__`` passes ``account_id=account_name`` to
    the base ``RiskManager`` (BL-20260617-PROP-RISK-ACCOUNT-ID) — the
    same path every regular account uses.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
        ``active_days``. Updated via :meth:`record_trade_result`, which
        persists through to ``runtime_state/prop_state.json`` (JSON is the
        source of truth; YAML is the fallback seed).
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

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        account_name: Optional[str] = None,
        dry_run: bool = False,
    ) -> None:
        # Wire account_name through as the base RiskManager's persistence
        # key (account_id) so prop accounts get the SAME journal-based
        # daily-risk-state self-healing rebuild every regular account
        # already gets (loader: RiskManager(..., account_id=name)). Without
        # it the base ran in-memory only, so a prop account's daily-loss /
        # drawdown caps reset to 0 on every restart — they never accumulated
        # across a session or survived a process bounce
        # (BL-20260617-PROP-RISK-ACCOUNT-ID). ``or ""`` preserves the
        # in-memory contract for nameless test/one-off constructions
        # (account_id="" disables persistence + the journal rebuild).
        super().__init__(
            config.get("risk") or config,
            dry_run=dry_run,
            account_id=account_name or "",
        )
        self.account_name: Optional[str] = account_name
        # Nominal account equity (e.g. the $5k 1-Step Classic size). A prop
        # account has NO live broker-balance API — it "executes" by emitting a
        # manual Telegram ticket — so the coordinator cannot supply a live
        # balance and ``_fetch_balance`` returns 0.0. This nominal is the
        # sizing/pre-screen basis used by ``position_size`` below when no live
        # balance is available (the placer recomputes the FINAL size against
        # the live platform balance from the ticket's risk framework).
        self.account_size_usd: float = float(config.get("account_size_usd") or 0.0)
        self.account_state: str = str(config.get("account_state") or "evaluation").lower()
        phase = config.get("phase_requirements") or {}
        self.target_profit_pct: float = float(phase.get("target_profit_pct", 0.05))
        self.min_active_days: int = int(phase.get("min_active_days", 4))
        self.min_daily_profit_pct: float = float(phase.get("min_daily_profit_pct", 0.0))

        # Prop-state persistence. The JSON file is
        # the live source of truth — it overrides the YAML seed when
        # present so a trader restart preserves mission progress.
        # YAML stays as the fallback seed for fresh installs / phase
        # resets. Counters update on every record_trade_result and
        # write through atomically.
        seed: Dict[str, Any] = dict(config.get("prop_state") or {})
        if account_name:
            from src.units.accounts.prop_state_io import load_prop_state
            persisted = load_prop_state(account_name)
            if persisted is not None:
                seed.update(persisted)  # JSON wins; YAML keys survive only when JSON lacks them
        self.cumulative_pnl_pct: float = float(seed.get("cumulative_pnl_pct") or 0.0)
        self.active_days: int = int(seed.get("active_days") or 0)
        self._entry_date_iso: Optional[str] = seed.get("entry_date") or None

        # Default to False so legacy prop fixtures (test_coordinator_flow,
        # test_s010_accounts) that pre-date the prop-risk integration
        # don't suddenly trip the overnight gate at certain UTC hours.
        # New prop accounts opt in by setting ``overnight_restricted:
        # true`` in their YAML.
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

    def position_size(
        self,
        package: OrderPackage,
        balance_usd: float,
        *,
        market_type: str = "spot",
        available_usd: Optional[float] = None,
        total_account_usd: Optional[float] = None,
        whole_units: bool = False,
    ) -> float:
        """Size a prop trade against the NOMINAL account equity when no live
        balance is available.

        A prop account has no live broker-balance API: it "executes" by
        emitting a manual Telegram ticket, so the coordinator's balance
        fetch (``_fetch_balance`` for ``exchange: breakout``) returns 0.0.
        The base ``RiskManager.position_size`` would then refuse on the
        ``gate_balance <= 0`` guard (the prop-account no-trades cause,
        BL-20260619-PROP-GATE-BALANCE) and the ticket would never be
        emitted.

        Prop sizing is intentionally split (operator design, 2026-06-19):
          * the BOT sizes + pre-screens against the **nominal** account
            equity (``current_equity`` if the journal rebuild seeded it,
            else the configured ``account_size_usd``) — so the nominal
            daily-loss / drawdown caps in the base gate still apply; and
          * the PLACER computes the **final** size against the live platform
            balance from the risk framework rendered on the ticket
            (``risk_pct`` × live balance ÷ risk-per-unit).

        So when ``balance_usd`` (and ``total_account_usd``) come through
        unavailable (``<= 0``/``None``), substitute the nominal equity and
        defer to the base sizer. The returned qty is a nominal suggestion;
        the breakout executor re-sizes the emitted ticket from the nominal
        itself and the placer finalizes — the coordinator only needs a
        positive qty to route the package to ``emit_prop_ticket``. When a
        live balance IS supplied (tests / a future balance source), the base
        behaviour is unchanged.
        """
        if _is_test_order(package):
            return super().position_size(
                package, balance_usd, market_type=market_type,
                available_usd=available_usd, total_account_usd=total_account_usd,
                whole_units=whole_units,
            )
        nominal = (
            self.current_equity
            if (self.current_equity and self.current_equity > 0)
            else self.account_size_usd
        )
        # Only substitute the nominal when NO live balance signal is present at
        # all (both the spot balance and the derivatives total come through
        # unavailable — the breakout case). A genuine live balance is left
        # untouched and sizes off the risk budget as usual (there is no
        # minimum-balance floor — min_balance_usd was removed 2026-06-24; only
        # a non-positive balance refuses, via the base gate_balance<=0 guard).
        has_live_balance = (balance_usd and balance_usd > 0) or (
            total_account_usd is not None and total_account_usd > 0
        )
        if nominal and nominal > 0 and not has_live_balance:
            balance_usd = nominal
            total_account_usd = nominal
        return super().position_size(
            package, balance_usd, market_type=market_type,
            available_usd=available_usd, total_account_usd=total_account_usd,
            whole_units=whole_units,
        )

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
    # State-update hooks (persist through to runtime_state/prop_state.json)
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

        Prop-state persistence: writes the updated counters to
        ``runtime_state/prop_state.json`` so the next trader restart
        resumes from the same mission progress. Best-effort — a write
        failure logs a warning and does NOT raise into the caller.
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

        self._persist_state()

    def _persist_state(self) -> None:
        """Write current counters through to ``prop_state.json``.

        Best-effort with a defensive outer try/except: even if
        ``write_prop_state`` itself raises (e.g. a misbehaving
        monkeypatch in a test, or an unexpected SDK exception), the
        in-process counters and the order path stay intact. The
        next ``record_trade_result`` retries the write.
        """
        if not self.account_name:
            # Tests / legacy fixtures construct the manager without an
            # account name — skip persistence rather than write to a
            # nameless slot.
            return
        try:
            from src.units.accounts.prop_state_io import write_prop_state
            write_prop_state(self.account_name, {
                "cumulative_pnl_pct": float(self.cumulative_pnl_pct),
                "active_days": int(self.active_days),
                "entry_date": self._entry_date_iso,
            })
        except Exception:  # noqa: BLE001
            # Already best-effort inside write_prop_state, but the
            # outer guard protects against monkeypatched-helper
            # surprises in tests and any unexpected import-time
            # failure.
            pass

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
