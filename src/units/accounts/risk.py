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

State persistence (A-1):
  - daily_pnl and daily_high_equity are written to a ``daily_risk_state``
    table in the trade journal DB on every change and reloaded on startup.
  - Persistence is keyed by ``account_id`` (the YAML account name, e.g.
    "bybit_2"). When ``account_id`` is empty the manager runs in-memory
    only (backward-compatible with existing tests and one-off callers).
  - DB path: TRADE_JOURNAL_DB env var, falling back to
    /data/bot-data/trade_journal.db (production default).
  - All DB ops are best-effort — a failure never blocks a trade.
"""
from __future__ import annotations

import math
import os
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

# Production trade-journal path (matches coordinator.py + deploy).
_DEFAULT_DB_PATH = "/data/bot-data/trade_journal.db"

_CREATE_DAILY_RISK_STATE = """
CREATE TABLE IF NOT EXISTS daily_risk_state (
    account_id       TEXT NOT NULL,
    date             TEXT NOT NULL,
    daily_pnl        REAL NOT NULL DEFAULT 0.0,
    daily_high_equity REAL,
    PRIMARY KEY (account_id, date)
)
"""


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
    contract_value_usd: float = 1.0,
) -> float:
    """Raw position-size calculation with no upper-bound clamp.

    ``contract_value_usd`` is the USD value of a 1-point price move for one
    unit/contract. It is ``1.0`` for crypto perps (a $1 price move on one
    coin-unit is $1), so the crypto path is unchanged. For futures like MES
    it is the contract multiplier ($5/index-point), so risk-per-contract is
    ``risk_distance × contract_value_usd`` and the qty comes out in whole
    contracts.
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

    cvu = float(contract_value_usd) if contract_value_usd else 1.0
    risk_usdt = balance_usdt * risk_pct
    raw_qty = risk_usdt / (risk_distance * cvu)
    floored = _floor_to_step(raw_qty, qty_precision)
    return max(min_qty, floored)


# Lazy cache of symbol -> contract_value_usd from config/instruments.yaml,
# so the sizing hot path avoids a YAML read per call. Defaults to 1.0 for
# any symbol without a profile, so the crypto path is unaffected even when
# instruments.yaml is missing or partial.
_CONTRACT_VALUE_CACHE: Optional[dict] = None


def contract_value_usd_for(symbol: str) -> float:
    """Return the USD-per-point contract value for *symbol* (default 1.0)."""
    global _CONTRACT_VALUE_CACHE
    if not symbol:
        return 1.0
    if _CONTRACT_VALUE_CACHE is None:
        try:
            from src.core.profile_loader import load_instrument_profiles
            profiles = load_instrument_profiles()
            _CONTRACT_VALUE_CACHE = {
                sym: float(getattr(p, "contract_value_usd", 1.0) or 1.0)
                for sym, p in (profiles or {}).items()
            }
        except Exception:  # noqa: BLE001
            _CONTRACT_VALUE_CACHE = {}
    return _CONTRACT_VALUE_CACHE.get(symbol, 1.0)


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

    def __init__(
        self,
        config: dict,
        *,
        dry_run: bool = False,
        account_id: str = "",
    ) -> None:
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
        # A-1: account name used as the persistence key. Empty string
        # disables persistence (backward-compat for tests + one-off callers).
        self.account_id: str = account_id
        self.daily_pnl: float = 0.0
        self.current_equity: Optional[float] = None
        self.daily_high_equity: Optional[float] = None
        self._last_reset_utc_date: Optional[Any] = self._today_utc()
        # Restore daily_pnl + daily_high_equity from the previous run.
        self._load_daily_state()

    @staticmethod
    def _today_utc():
        return datetime.now(timezone.utc).date()

    @staticmethod
    def _risk_db_path() -> str:
        return os.environ.get("TRADE_JOURNAL_DB") or _DEFAULT_DB_PATH

    def _ensure_state_table(self, conn: Any) -> None:
        conn.execute(_CREATE_DAILY_RISK_STATE)

    def _load_daily_state(self) -> None:
        """Restore today's daily_pnl + daily_high_equity from SQLite.

        No-op when account_id is empty or the DB is unavailable.
        """
        if not self.account_id:
            return
        try:
            import sqlite3
            today = str(self._today_utc())
            with sqlite3.connect(self._risk_db_path(), timeout=5) as conn:
                self._ensure_state_table(conn)
                row = conn.execute(
                    "SELECT daily_pnl, daily_high_equity "
                    "FROM daily_risk_state "
                    "WHERE account_id=? AND date=?",
                    (self.account_id, today),
                ).fetchone()
            if row:
                self.daily_pnl = float(row[0])
                self.daily_high_equity = float(row[1]) if row[1] is not None else None
        except Exception:
            pass  # DB unavailable at startup — stay in-memory

    def _save_daily_state(self) -> None:
        """Persist current daily_pnl + daily_high_equity to SQLite.

        Best-effort — a DB write failure never blocks a trade.
        """
        if not self.account_id:
            return
        try:
            import sqlite3
            today = str(self._today_utc())
            with sqlite3.connect(self._risk_db_path(), timeout=5) as conn:
                self._ensure_state_table(conn)
                conn.execute(
                    """INSERT INTO daily_risk_state
                           (account_id, date, daily_pnl, daily_high_equity)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(account_id, date) DO UPDATE SET
                           daily_pnl=excluded.daily_pnl,
                           daily_high_equity=excluded.daily_high_equity""",
                    (self.account_id, today, self.daily_pnl, self.daily_high_equity),
                )
        except Exception:
            pass  # Best-effort — never let a DB write stop a trade

    def _maybe_roll_daily(self) -> None:
        today = self._today_utc()
        if self._last_reset_utc_date is None or today > self._last_reset_utc_date:
            self.daily_pnl = 0.0
            self.daily_high_equity = self.current_equity
            self._last_reset_utc_date = today
            self._save_daily_state()

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
        self._save_daily_state()

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
        be OPENED with the account's available margin. Two paths:

        Live figure (``available_usd`` is not None — linear-perp
        accounts where the coordinator fetched ``availableToWithdraw``
        from the Bybit UNIFIED API):

            max_qty_by_margin = (available_usd * effective_leverage)
                                 / package.entry

        Buffer fallback (``available_usd`` is None — spot accounts,
        dry-run, or any fetch failure):

            max_qty_by_margin = (balance_usd * effective_leverage *
                                 _MARGIN_SAFETY_BUFFER) / package.entry

        The live figure is more accurate because it reflects existing
        open positions consuming margin. The buffer fallback is always
        present so there is a ceiling even when the exchange call fails.
        When the risk-based qty exceeds ``max_qty_by_margin``, qty is
        floor-rounded down to fit. When even min_qty would exceed the
        ceiling, the sizer returns 0.0 — the executor sees a per-trade
        refusal instead of a downstream Bybit ErrCode 110007.

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

        # Per-instrument contract value ($/point). 1.0 for crypto (path
        # unchanged); the MES contract multiplier ($5/point) for futures so
        # qty comes out in whole contracts and the USD-loss math below is
        # correct.
        cvu = contract_value_usd_for(getattr(package, "symbol", "") or "")

        qty = _size_unbounded(
            package,
            risk_pct=effective_risk_pct,
            balance_usdt=balance_usd,
            min_qty=self.min_qty,
            qty_precision=self.qty_precision,
            contract_value_usd=cvu,
        )

        # S-026 G3: daily-loss-budget gate. USD loss at SL is
        # qty × risk_distance × contract_value_usd (cvu=1.0 for crypto).
        self._maybe_roll_daily()
        loss_budget_remaining = self.max_daily_loss_usd + self.daily_pnl
        if loss_budget_remaining <= 0:
            return 0.0

        risk_distance = abs(package.entry - package.sl)
        max_loss_at_sl = qty * risk_distance * cvu
        if max_loss_at_sl > loss_budget_remaining:
            scaled = loss_budget_remaining / (risk_distance * cvu)
            qty = _floor_to_step(scaled, self.qty_precision)
            if qty < self.min_qty:
                return 0.0

        # === 2026-05-12 margin pre-flight cap ===
        # Crypto-specific: caps qty by notional/leverage using price as the
        # per-unit notional. Futures margin is per-contract SPAN/initial
        # margin (not price×qty/leverage), and the broker rejects orders that
        # exceed available margin at submit time — so this crypto cap is
        # skipped for futures market types to avoid a wrong ceiling.
        effective_leverage = self.leverage if self.leverage > 0 else 1
        if market_type != "futures" and package.entry > 0:
            if available_usd is not None:
                max_qty_by_margin = (available_usd * effective_leverage) / package.entry
            else:
                max_qty_by_margin = (
                    balance_usd * effective_leverage * _MARGIN_SAFETY_BUFFER
                ) / package.entry
            if qty > max_qty_by_margin:
                capped = _floor_to_step(max_qty_by_margin, self.qty_precision)
                if capped < self.min_qty:
                    return 0.0
                qty = capped

        return qty

    def reset_daily(self) -> None:
        self.daily_pnl = 0.0
        self.daily_high_equity = self.current_equity
        self._last_reset_utc_date = self._today_utc()
        self._save_daily_state()

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
