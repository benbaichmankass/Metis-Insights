"""Per-account risk manager — units layer (S-008 PR #122 / S-010 PR #1 /
S-012 PR E3a max_dd_pct enforcement / S-026 G2 single-sizer contract /
S-026 G3 floor-rounding + daily-loss-budget gate /
2026-05-12 margin pre-flight cap to surface 110007 as a RiskManager refusal).

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

# 2026-05-12 margin pre-flight (see RiskManager.position_size). Headroom
# for fees + Bybit's maintenance-margin buffer so the available-balance
# math doesn't put us right at the edge where one tick of unrealized PnL
# would push the resulting position into a margin-call state.
# 0.9 = use up to 90% of available margin for a new position.
_MARGIN_SAFETY_BUFFER = 0.9


def _is_test_order(pkg: "OrderPackage") -> bool:
    """Return True when *pkg* is a smoke-test order (meta.is_test=True)."""
    if not getattr(pkg, "meta", None):
        return False
    return bool(pkg.meta.get("is_test"))


def _floor_to_step(value: float, precision: int) -> float:
    """Round *value* DOWN to *precision* decimal places.

    S-026 G3: sizing must always round toward zero so the realised
    risk never exceeds the configured cap.
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
    """Raw position-size calculation with no upper-bound clamp."""
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
    """Backwards-compatible wrapper. New callers should use
    RiskManager.position_size(pkg, balance_usd)."""
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
    """Build a RiskManager from *account_cfg* and delegate to position_size."""
    rm = RiskManager(account_cfg)
    market_type = str(account_cfg.get("market_type") or "spot").strip().lower()
    return rm.position_size(pkg, balance_usdt, market_type=market_type)


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
    """

    def __init__(self, config: dict, *, dry_run: bool = False) -> None:
        self.max_dd_pct: float = float(config.get("max_dd_pct", 0.05))
        self.max_daily_loss_usd: float = float(config.get("daily_usd", 100.0))
        self.max_pos_size_usd: float = float(config.get("pos_size", 500.0))
        self.risk_pct: float = float(config.get("risk_pct", 0.01))
        self.min_balance_usd: float = float(config.get("min_balance_usd", 50.0))
        self.min_qty: float = float(config.get("min_qty", _DEFAULT_MIN_QTY))
        self.qty_precision: int = int(config.get("qty_precision", _DEFAULT_QTY_PRECISION))
        # PR 3 cutover: per-account leverage for linear-perp accounts.
        # 2026-05-12: leverage is also read by position_size() for the
        # margin pre-flight cap (see method docstring).
        self.leverage: int = int(config.get("leverage", 0) or 0)
        self.dry_run: bool = bool(dry_run)
        self.daily_pnl: float = 0.0
        self.current_equity: Optional[float] = None
        self.daily_high_equity: Optional[float] = None
        self._last_reset_utc_date: Optional[Any] = self._today_utc()

    @staticmethod
    def _today_utc():
        return datetime.now(timezone.utc).date()

    def _maybe_roll_daily(self) -> None:
        today = self._today_utc()
        if self._last_reset_utc_date is None or today > self._last_reset_utc_date:
            self.daily_pnl = 0.0
            self.daily_high_equity = self.current_equity
            self._last_reset_utc_date = today

    def update_equity(self, equity_usd: float) -> None:
        self._maybe_roll_daily()
        self.current_equity = float(equity_usd)
        if (
            self.daily_high_equity is None
            or self.current_equity > self.daily_high_equity
        ):
            self.daily_high_equity = self.current_equity

    def intraday_drawdown(self) -> Optional[float]:
        if self.daily_high_equity is None or self.current_equity is None:
            return None
        if self.daily_high_equity <= 0:
            return None
        if self.current_equity >= self.daily_high_equity:
            return 0.0
        return (self.daily_high_equity - self.current_equity) / self.daily_high_equity

    def approve(self, order: OrderPackage) -> bool:
        ok, _reason = self.evaluate(order)
        return ok

    def evaluate(self, order: OrderPackage) -> tuple[bool, Optional[str]]:
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
        decides position size.

        2026-05-12 — margin pre-flight cap:
        ---------------------------------
        After risk-based sizing and the daily-loss-budget gate, an
        additional check verifies the resulting position can actually
        be OPENED with the account's available margin. The
        2026-05-12 incident exposed the gap: risk-based sizing
        produced a $729 notional against a $158 wallet at 3x
        leverage; required IM was $243, wallet had $158, Bybit
        returned ErrCode 110007 ("ab not enough for new order").
        Now:

            max_qty_by_margin = (balance_usd * effective_leverage *
                                 _MARGIN_SAFETY_BUFFER) / package.entry

        where ``effective_leverage`` is ``self.leverage`` for linear-
        perp accounts and ``1`` for cash spot. When the risk-based qty
        exceeds ``max_qty_by_margin``, qty is floor-rounded down to
        fit. When even the min_qty would exceed available margin, the
        sizer returns 0.0 — the executor sees a per-trade refusal
        (with a verbatim reason via the standard refusal wire) instead
        of dispatching a guaranteed-to-fail order. Per the Prime
        Directive in docs/CLAUDE-RULES-CANONICAL.md, this surfaces the
        condition as a per-trade RiskManager refusal (account stays
        live, operator gets Telegram per trade) rather than as a
        Bybit-side ErrCode that the (now-deleted) breaker would have
        used to flip the account dry.

        Smoke-test orders (``meta.is_test=True``) bypass risk-based
        sizing and use ``meta.test_qty`` (default _DEFAULT_TEST_QTY).
        """
        if _is_test_order(package):
            return float((package.meta or {}).get("test_qty") or _DEFAULT_TEST_QTY)

        gate_balance = (
            total_account_usd if total_account_usd is not None else balance_usd
        )
        if gate_balance < self.min_balance_usd:
            return 0.0

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

        # S-026 G3: daily-loss-budget gate.
        self._maybe_roll_daily()
        loss_budget_remaining = self.max_daily_loss_usd + self.daily_pnl
        if loss_budget_remaining <= 0:
            return 0.0

        risk_distance = abs(package.entry - package.sl)
        max_loss_at_sl = qty * risk_distance
        if max_loss_at_sl > loss_budget_remaining:
            scaled = loss_budget_remaining / risk_distance
            qty = _floor_to_step(scaled, self.qty_precision)
            if qty < self.min_qty:
                return 0.0

        # === 2026-05-12 margin pre-flight cap ===
        # Verify the resulting position can actually be opened with the
        # account's available margin. If risk-based qty exceeds what
        # (balance × leverage × buffer) can support, scale down. If even
        # min_qty wouldn't fit, refuse the trade.
        effective_leverage = self.leverage if self.leverage > 0 else 1
        if package.entry > 0:
            max_qty_by_margin = (
                balance_usd * effective_leverage * _MARGIN_SAFETY_BUFFER
            ) / package.entry
            if qty > max_qty_by_margin:
                capped = _floor_to_step(max_qty_by_margin, self.qty_precision)
                if capped < self.min_qty:
                    # Account too small to open even the min_qty lot at
                    # this entry price with the configured leverage.
                    # Refuse the trade — the operator gets a per-trade
                    # Telegram via the standard refusal wire instead of
                    # a downstream Bybit ErrCode 110007.
                    return 0.0
                qty = capped

        del available_usd  # parameter retained for backward compat

        return qty

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.daily_high_equity = self.current_equity
        self._last_reset_utc_date = self._today_utc()

    def report(self) -> dict:
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
