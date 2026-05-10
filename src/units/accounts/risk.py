"""Per-account risk manager — units layer (S-008 PR #122 / S-010 PR #1 /
S-012 PR E3a max_dd_pct enforcement / S-026 G2 single-sizer contract /
S-026 G3 floor-rounding + daily-loss-budget gate).

Two interfaces:
  - Functional (S-008): size_order() / size_order_from_cfg() — kept as a
    backwards-compatible wrapper that now delegates to
    RiskManager.position_size().
  - Class-based (S-010 + S-012 E3a + S-026 G2): RiskManager — the only
    place that decides position size. Used by TradingAccount.place_order()
    and (post G2) by Coordinator.multi_account_execute() per account.

The class-based interface tracks per-account state:
  - daily_pnl: USD PnL since the last reset
  - current_equity / daily_high_equity: equity tracking for intra-day
    drawdown (PM § 8 #6 — resets at UTC midnight on the next approve()
    or update_equity() call).

Hard limits (from accounts.yaml ``risk`` section):
  - max_dd_pct: max intra-day equity drawdown from today's high (S-012 PR E3a)
  - daily_usd: max daily loss in USD (S-010)
  - pos_size: max single-position size in USD (S-010) — applied by
    approve() against ``order.meta['estimated_value']``; **not** used as
    a clamp inside position_size() per operator directive (S-026 G2:
    "no hard-coded max position, just balance %").

Sizing inputs (also from the ``risk`` section):
  - risk_pct: fraction of balance risked per trade (operator default 0.01)
  - min_balance_usd: refuse to size below this balance (operator default 50)
  - leverage: per-account leverage for linear-perp accounts (PR 3
    cutover). 0 means "not configured" — set_leverage is skipped at
    startup. Cash spot accounts ignore this field.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional
from src.core.coordinator import OrderPackage


_DEFAULT_MIN_QTY = 0.001    # BTC minimum lot size
_DEFAULT_MAX_QTY = 100.0    # hard cap; override via account cfg
_DEFAULT_QTY_PRECISION = 3

# Default smoke-test qty when meta.is_test=True but meta.test_qty is missing.
# Below Bybit linear perp min-lot (0.001 BTC) so the exchange rejects.
_DEFAULT_TEST_QTY = 0.0001

# PR 5 (2026-05-10): spot-margin sizing-parameter defaults
# (DEFAULT_MAX_BORROW_BTC / DEFAULT_BORROW_FEE_APR_PCT /
# DEFAULT_LIQUIDATION_BUFFER_PCT / DEFAULT_SPOT_MARGIN_LTV) were
# removed alongside the spot-margin sizing kernel. They only fired
# when ``market_type == "spot-margin"`` (no production account post
# PR 3 — bybit_2 now trades USDT-margined linear perps at 3×).


def _is_test_order(pkg: "OrderPackage") -> bool:
    """Return True when *pkg* is a smoke-test order (meta.is_test=True).

    A test order short-circuits both risk approval (RiskManager.approve)
    and risk-based sizing (size_order_from_cfg). The executor uses
    meta.test_qty directly. The whole point is to exercise the live
    plumbing path without sizing real risk into the account.
    """
    if not getattr(pkg, "meta", None):
        return False
    return bool(pkg.meta.get("is_test"))


# ---------------------------------------------------------------------------
# Functional interface (S-008 — backward-compatible)
# ---------------------------------------------------------------------------


def _floor_to_step(value: float, precision: int) -> float:
    """Round *value* DOWN to *precision* decimal places.

    S-026 G3: sizing must always round toward zero so the realised
    risk never exceeds the configured cap. Python's built-in
    ``round()`` uses banker's rounding (and rounds 0.5 up), which can
    push an order one step-size over the budget; ``floor`` is the
    safer choice for risk math.
    """
    if precision < 0:
        raise ValueError(f"precision must be >= 0, got {precision}")
    if value <= 0:
        return 0.0
    factor = 10 ** precision
    return math.floor(value * factor) / factor


def _size_unbounded(
    pkg: OrderPackage,
    *,
    risk_pct: float,
    balance_usdt: float,
    min_qty: float = _DEFAULT_MIN_QTY,
    qty_precision: int = _DEFAULT_QTY_PRECISION,
) -> float:
    """Raw position-size calculation with no upper-bound clamp.

    S-026 G2: this is the math kernel both ``size_order`` (legacy, with
    optional max_qty clamp) and ``RiskManager.position_size`` (canonical,
    no clamp per operator directive) call into. Exposed as a private
    helper so the two paths can't drift.

    S-026 G3: switched to floor-rounding (``_floor_to_step``) so the
    realised risk never exceeds the configured cap by one step-size.
    """
    if balance_usdt <= 0:
        raise ValueError(f"balance_usdt must be positive, got {balance_usdt}")
    if risk_pct <= 0:
        raise ValueError(f"risk_pct must be positive, got {risk_pct}")

    risk_distance = abs(pkg.entry - pkg.sl)
    if risk_distance == 0:
        raise ValueError(
            f"OrderPackage entry ({pkg.entry}) equals sl ({pkg.sl}); "
            "cannot compute position size (division by zero)."
        )

    risk_usdt = balance_usdt * risk_pct
    raw_qty = risk_usdt / risk_distance
    floored = _floor_to_step(raw_qty, qty_precision)
    # min_qty is the exchange-min lot; orders below it get rejected at
    # submission. We honour the floor so the order is exchange-acceptable
    # even when raw_qty is below it.
    return max(min_qty, floored)


def size_order(
    pkg: OrderPackage,
    risk_pct: float,
    balance_usdt: float,
    *,
    min_qty: float = _DEFAULT_MIN_QTY,
    max_qty: float = _DEFAULT_MAX_QTY,
    qty_precision: int = _DEFAULT_QTY_PRECISION,
) -> float:
    """Return the position size (qty) for *pkg* given the account constraints.

    S-026 G2: kept as a backwards-compatible wrapper. New callers should
    use ``RiskManager.position_size(pkg, balance_usd)`` — the single
    sizing site post-G2. This freestanding function is preserved for
    callers that still construct sizing from a plain dict (smoke-test
    helpers, backtest harnesses).

    Parameters
    ----------
    pkg : OrderPackage
        Contains entry and sl prices for risk calculation.
    risk_pct : float
        Fraction of balance to risk (e.g., 0.01 = 1 %).
    balance_usdt : float
        Current USDT balance of the account.
    min_qty : float
        Minimum tradeable quantity (default 0.001 BTC).
    max_qty : float
        Maximum allowed quantity. Note: operator directive S-026 G2
        removes the max-qty clamp from the canonical sizer
        (``RiskManager.position_size``); this freestanding helper
        retains it for backwards compatibility.
    qty_precision : int
        Number of decimal places for rounding.

    Returns
    -------
    float
        Sized, clipped, and rounded quantity.

    Raises
    ------
    ValueError
        When balance or risk_pct are non-positive, or when entry == sl.
    """
    raw = _size_unbounded(
        pkg,
        risk_pct=risk_pct,
        balance_usdt=balance_usdt,
        min_qty=min_qty,
        qty_precision=qty_precision,
    )
    return round(min(raw, max_qty), qty_precision)


def size_order_from_cfg(
    pkg: OrderPackage,
    account_cfg: dict,
    balance_usdt: float,
) -> float:
    """Convenience wrapper: build a RiskManager from *account_cfg* and
    delegate sizing to its ``position_size`` method.

    S-026 G2: this used to call ``size_order`` directly with values
    pulled from the dict; after G2 the only sizer is
    ``RiskManager.position_size`` so this wrapper now constructs an
    ephemeral RiskManager from the dict and forwards.

    Smoke-test orders (``pkg.meta['is_test']`` is True) skip risk-based
    sizing entirely and return ``pkg.meta['test_qty']`` (default
    ``_DEFAULT_TEST_QTY``). The qty is intentionally below Bybit's
    min-lot so the exchange rejects on submission — the rejection is
    the success signal for the live-plumbing test.

    The dict's keys mirror the YAML schema:
      - ``risk_pct`` (default 0.01)
      - ``min_balance_usd`` (default 50)
      - ``min_qty`` (default 0.001)
      - ``qty_precision`` (default 3)
      - ``market_type`` (default ``"spot"``) — forwarded to
        ``position_size``. Retained from S-047 T3 D5; with the
        spot-margin kernel removed in PR 5 it no longer triggers any
        extra rules, but the parameter is kept so direct
        ``account_execute`` callers stay source-compatible.
    Plus the legacy ``risk:`` sub-keys (``max_dd_pct``, ``daily_usd``,
    ``pos_size``) which RiskManager ignores for sizing.
    """
    rm = RiskManager(account_cfg)
    market_type = str(account_cfg.get("market_type") or "spot").strip().lower()
    return rm.position_size(pkg, balance_usdt, market_type=market_type)


# ---------------------------------------------------------------------------
# Class-based interface (S-010 — stateful per-account risk enforcement)
# ---------------------------------------------------------------------------


class RiskManager:
    """Per-account risk gate with stateful daily-PnL tracking.

    Parameters (from accounts.yaml ``risk`` section)
    -------------------------------------------------
    max_dd_pct : float
        Maximum drawdown as a fraction of starting equity (e.g., 0.05 = 5 %).
    daily_usd : float
        Maximum allowed daily loss in USD (e.g., 100).
    pos_size : float
        Maximum single-position size in USD (e.g., 500).
        Checked against ``order.meta['estimated_value']`` when present.
    """

    def __init__(self, config: dict, *, dry_run: bool = False) -> None:
        self.max_dd_pct: float = float(config.get("max_dd_pct", 0.05))
        self.max_daily_loss_usd: float = float(config.get("daily_usd", 100.0))
        self.max_pos_size_usd: float = float(config.get("pos_size", 500.0))
        # S-026 G2: sizing inputs. Operator-confirmed defaults: 1% risk
        # per trade, refuse to size below $50 balance.
        self.risk_pct: float = float(config.get("risk_pct", 0.01))
        self.min_balance_usd: float = float(config.get("min_balance_usd", 50.0))
        # Optional sizing-shape overrides (per account). When absent the
        # module-level defaults apply.
        self.min_qty: float = float(config.get("min_qty", _DEFAULT_MIN_QTY))
        self.qty_precision: int = int(config.get("qty_precision", _DEFAULT_QTY_PRECISION))
        # PR 3 cutover: per-account leverage for linear-perp accounts.
        # When the account has `market_type: linear`,
        # main.py::_apply_per_account_leverage reads this attribute and
        # calls `/v5/position/set-leverage` once per (symbol, account)
        # at boot. 0 means "not configured" → set_leverage is skipped;
        # the exchange uses whatever leverage was last set
        # (Bybit-side persistent setting). Cash spot accounts can
        # leave this at 0.
        self.leverage: int = int(config.get("leverage", 0) or 0)
        # The single dry/live toggle in the codebase (operator directive
        # 2026-05-03). Set from accounts.yaml `mode: live | dry_run` at
        # construction; flippable at runtime via Coordinator.set_account_dry_run().
        # When True, evaluate() returns reason="account_mode_dry_run" so
        # the executor records the rejection in the trade journal but
        # never calls the exchange.
        self.dry_run: bool = bool(dry_run)
        self.daily_pnl: float = 0.0       # updated by record_trade_result()
        # S-012 PR E3a — intra-day drawdown tracking. None until the
        # caller seeds equity via update_equity(); the drawdown check
        # is skipped while equity is unknown so the field remains
        # backwards-compatible with callers that don't track equity.
        self.current_equity: Optional[float] = None
        self.daily_high_equity: Optional[float] = None
        self._last_reset_utc_date: Optional[Any] = self._today_utc()

    @staticmethod
    def _today_utc():
        """Return today's UTC date (timezone-aware)."""
        return datetime.now(timezone.utc).date()

    def _maybe_roll_daily(self) -> None:
        """If the UTC date has advanced past the last reset, reset daily state.

        Called at the top of approve() / update_equity() so the rollover
        happens lazily without a scheduler. PM § 8 #6: resets at UTC
        midnight.
        """
        today = self._today_utc()
        if self._last_reset_utc_date is None or today > self._last_reset_utc_date:
            self.daily_pnl = 0.0
            # Re-anchor the intra-day high to current_equity (or None).
            self.daily_high_equity = self.current_equity
            self._last_reset_utc_date = today

    def update_equity(self, equity_usd: float) -> None:
        """Set the account's current equity in USD.

        Bumps daily_high_equity when the new value is a fresh intra-day
        high. Idempotent on the same equity value. Roll the daily window
        first so a new UTC day re-anchors the high.
        """
        self._maybe_roll_daily()
        self.current_equity = float(equity_usd)
        if (
            self.daily_high_equity is None
            or self.current_equity > self.daily_high_equity
        ):
            self.daily_high_equity = self.current_equity

    def intraday_drawdown(self) -> Optional[float]:
        """Return the current intra-day drawdown as a fraction (0.0 .. 1.0).

        Returns None when equity has not been seeded (no signal). When
        current_equity exceeds daily_high_equity, drawdown is clamped at 0.
        """
        if self.daily_high_equity is None or self.current_equity is None:
            return None
        if self.daily_high_equity <= 0:
            return None
        if self.current_equity >= self.daily_high_equity:
            return 0.0
        return (self.daily_high_equity - self.current_equity) / self.daily_high_equity

    def approve(self, order: OrderPackage) -> bool:
        """Return True when the order passes all risk checks.

        Thin wrapper over :meth:`evaluate` — kept for the existing
        callers (``TradingAccount.place_order`` legacy path) that only
        care about the boolean. New callers should prefer ``evaluate``
        which carries a structured skip reason for logging.
        """
        ok, _reason = self.evaluate(order)
        return ok

    def evaluate(self, order: OrderPackage) -> tuple[bool, Optional[str]]:
        """Return ``(allow, reason)`` for *order*.

        ``reason`` is None on accept; on reject it is a short stable
        token suitable for logging / Telegram surfaces:
        ``DAILY_LOSS_CAP``, ``POSITION_SIZE_CAP``, ``INTRADAY_DRAWDOWN``.
        Subclasses (``PropRiskManager``) extend the reason vocabulary —
        see ``src/units/accounts/prop_risk.py`` for ``SKIP_MISSION_MET``,
        ``SKIP_OVERNIGHT_RESTRICTED``, ``SKIP_WEEKEND_RESTRICTED``.

        Smoke-test orders (``order.meta['is_test']`` is True) bypass
        every gate below — they are intentionally tiny payloads
        designed to exercise the exchange-rejection path.

        Checks (in order, real orders only):
          0. Account mode (the single dry/live toggle in the repo,
             operator directive 2026-05-03): ``self.dry_run`` is True →
             reject with reason ``"account_mode_dry_run"``. The executor
             still logs a row to the trade journal so the operator can
             see what *would have* fired; the exchange is not called.
          1. UTC daily rollover (resets daily_pnl + re-anchors high).
          2. Daily loss limit: ``daily_pnl < -max_daily_loss_usd`` → reject.
          3. Position size: ``order.meta['estimated_value'] >
             max_pos_size_usd`` → reject.
          4. Intra-day drawdown (S-012 PR E3a): when equity is known,
             ``(daily_high - current) / daily_high >= max_dd_pct`` → reject.
             Skipped when equity has not been seeded via update_equity().
        """
        if _is_test_order(order):
            return True, None

        if self.dry_run:
            return False, "account_mode_dry_run"

        self._maybe_roll_daily()

        if self.daily_pnl < -self.max_daily_loss_usd:
            return False, "DAILY_LOSS_CAP"

        estimated_value = order.meta.get("estimated_value") if order.meta else None
        if estimated_value is not None and float(estimated_value) > self.max_pos_size_usd:
            return False, "POSITION_SIZE_CAP"

        dd = self.intraday_drawdown()
        if dd is not None and dd >= self.max_dd_pct:
            return False, "INTRADAY_DRAWDOWN"

        return True, None

    def record_trade_result(self, pnl_usd: float) -> None:
        """Update daily PnL after a trade closes.  Call this from the accounts unit.

        When equity is being tracked, update_equity() should also be
        called by the caller with the post-trade equity value so the
        intra-day high stays current.
        """
        self._maybe_roll_daily()
        self.daily_pnl += pnl_usd

    def position_size(
        self,
        package: OrderPackage,
        balance_usd: float,
        *,
        market_type: str = "spot",
        available_usd: Optional[float] = None,
        total_account_usd: Optional[float] = None,
    ) -> float:
        """Return the qty to trade for *package* given *balance_usd*.

        S-026 G2: this is the **only** function in the codebase that
        decides position size. Inputs are the strategy's trade idea
        (entry/sl/tp) and the per-account balance; output is qty in
        base-asset units. Per-account risk parameters
        (``risk_pct``, ``min_balance_usd``, ``min_qty``,
        ``qty_precision``) come from this RiskManager instance —
        which is itself loaded from the account's ``risk:`` block in
        ``config/accounts.yaml``.

        Smoke-test orders (``meta.is_test=True``) bypass risk-based
        sizing and use ``meta.test_qty`` (default
        ``_DEFAULT_TEST_QTY``); the qty is intentionally below
        Bybit's min-lot so the exchange rejects on submission.

        Returns 0.0 (and logs a warning at the call site via the
        normal sizing-skipped path) when balance is below
        ``min_balance_usd`` — the account is too small to size into a
        meaningful position.

        ``available_usd`` is retained in the signature for backward
        compatibility with callers updated to pass it (a remnant of
        the pre-PR-5 spot-margin notional cap). With spot-margin
        sizing removed it is unused — sizing is the same for cash
        spot and linear perp accounts (the only two market_types in
        production post-PR 3). PR 3 cutover: linear perp accounts
        (``market_type: linear``) pass through here — same risk-based
        sizing math as cash spot.

        Notes
        -----
        - No hard-coded max-position cap (operator directive S-026 G2).
        - Per-strategy risk allocation (``meta.strategy_risk_pct``,
          recorded by the multiplexer in S-026 G1) is multiplied into
          ``risk_pct`` so two strategies on the same account split the
          per-trade risk budget instead of doubling it.
        - The exchange min-lot floor (``min_qty``) and step-size
          rounding (``qty_precision``) are applied here so the quote
          submitted to the exchange is always exchange-acceptable.
        - **Daily-loss budget gate (S-026 G3):** if a full SL hit on
          this trade would push ``daily_pnl`` past
          ``-max_daily_loss_usd``, the qty is scaled down to fit the
          remaining budget. If even ``min_qty`` would bust the budget,
          the sizer returns 0.0 and the order is refused.
        - **Floor rounding (S-026 G3):** the step-size rounding is
          *floor* not banker's, so the realised risk never exceeds the
          configured cap by one step.
        """
        if _is_test_order(package):
            return float((package.meta or {}).get("test_qty") or _DEFAULT_TEST_QTY)

        # S-052: the min_balance_usd gate ("is this account big enough?")
        # checks the operator's *total* account equity, not free
        # quote-coin balance. With spot accounts, ``balance_usd``
        # carries free USDT (the sizer's collateral input — see the
        # direction-aware override in coordinator.py), which under-counts
        # capital held as locked USDT, free BTC, or locked BTC. Pass
        # ``total_account_usd`` (Bybit wallet ``totalEquity`` —
        # free+locked across all coins, excluding borrow capacity) to
        # gate against the right thing. When None — the pre-S-052
        # contract — fall back to ``balance_usd`` so callers that
        # haven't been updated keep current behaviour byte-for-byte.
        gate_balance = (
            total_account_usd if total_account_usd is not None else balance_usd
        )
        if gate_balance < self.min_balance_usd:
            return 0.0

        # Per-strategy allocation (set by the multiplexer in pipeline.py).
        # Defaults to 1.0 — single-strategy accounts get full risk_pct.
        strategy_risk_pct = float(
            (package.meta or {}).get("strategy_risk_pct") or 1.0
        )
        effective_risk_pct = self.risk_pct * strategy_risk_pct

        qty = _size_unbounded(
            package,
            risk_pct=effective_risk_pct,
            balance_usdt=balance_usd,
            min_qty=self.min_qty,
            qty_precision=self.qty_precision,
        )

        # S-026 G3: daily-loss-budget gate. Roll the daily window first
        # (so a fresh UTC day re-opens the budget) and then verify that
        # if this trade hits its SL, the resulting daily_pnl still sits
        # above -max_daily_loss_usd. Scale down or refuse otherwise.
        self._maybe_roll_daily()
        loss_budget_remaining = self.max_daily_loss_usd + self.daily_pnl
        if loss_budget_remaining <= 0:
            # Already past the daily loss cap — sizer refuses any new
            # trade. (RiskManager.approve will also refuse, but blocking
            # here saves the routing detour.)
            return 0.0

        risk_distance = abs(package.entry - package.sl)
        max_loss_at_sl = qty * risk_distance
        if max_loss_at_sl > loss_budget_remaining:
            # Scale qty down to exactly fit the remaining budget,
            # then floor-round to the exchange step-size. If the
            # floored qty is below min_qty, the trade is too big for
            # the remaining budget — refuse.
            scaled = loss_budget_remaining / risk_distance
            qty = _floor_to_step(scaled, self.qty_precision)
            if qty < self.min_qty:
                return 0.0

        # PR 5 (2026-05-10): spot-margin sizing kernel removed.
        # ``market_type == "spot-margin"`` no longer triggers any extra
        # rules — bybit_2 routes through linear perps post-PR 3 and no
        # other account uses spot-margin. ``available_usd`` is still
        # accepted to preserve the caller signature but is unused.
        del available_usd  # parameter retained for backward compat

        return qty

    def reset_daily(self) -> None:
        """Manually reset daily PnL + intra-day high (UTC-day-independent).

        The lazy UTC rollover via _maybe_roll_daily() handles normal
        midnight resets. This method is kept for explicit operator
        intervention (Telegram /reset_daily etc.) and for tests.
        """
        self.daily_pnl = 0.0
        self.daily_high_equity = self.current_equity
        self._last_reset_utc_date = self._today_utc()

    def report(self) -> dict:
        """Return a human-readable status dict."""
        dd = self.intraday_drawdown()
        return {
            "daily_pnl": round(self.daily_pnl, 2),
            "max_daily_loss_usd": self.max_daily_loss_usd,
            "max_pos_size_usd": self.max_pos_size_usd,
            "max_dd_pct": self.max_dd_pct,
            "daily_loss_remaining": round(
                self.max_daily_loss_usd + self.daily_pnl, 2
            ),
            "current_equity": (
                round(self.current_equity, 2) if self.current_equity is not None else None
            ),
            "daily_high_equity": (
                round(self.daily_high_equity, 2) if self.daily_high_equity is not None else None
            ),
            "intraday_drawdown_pct": round(dd, 4) if dd is not None else None,
            "halted": (
                self.daily_pnl < -self.max_daily_loss_usd
                or (dd is not None and dd >= self.max_dd_pct)
            ),
        }
