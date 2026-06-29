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

There is NO position-notional ceiling: the removed ``pos_size`` /
``POSITION_SIZE_CAP`` capped a correctly risk-sized trade on a number
unrelated to account capacity, so it does not exist (operator directive
2026-06-24). Size is bounded only by the risk budget, the daily-loss
budget, the margin/buying-power ceiling, and the exchange's lot size.

Sizing inputs (also from the ``risk`` section):
  - risk_pct: fraction of balance risked per trade (operator default 0.01)
  - leverage: per-account leverage for linear-perp accounts (PR 3
    cutover). 0 means "not configured" — set_leverage is skipped at
    startup. Cash spot accounts ignore this field.

State persistence (A-1 + self-healing rebuild):
  - daily_pnl and daily_high_equity are persisted to a ``daily_risk_state``
    table in the canonical trade-journal DB and reloaded on startup.
  - SELF-HEALING: rather than depending on a runtime caller of
    ``record_trade_result()`` / ``update_equity()`` (there were none — the
    bug that left ``daily_risk_state`` empty and the daily-loss /
    max-drawdown caps reset to 0 on every restart), the manager rebuilds
    today's state from authoritative sources on init and on every gate
    check: realized PnL is summed from ``trades`` (this account, closed,
    today UTC by open date) and current equity is read from
    ``runtime_logs/balance_snapshots.json``. The reconciled state is
    persisted so a row always exists for today.
  - Persistence is keyed by ``account_id`` (the YAML account name, e.g.
    "bybit_2"). When ``account_id`` is empty the manager runs in-memory
    only (backward-compatible with existing tests and one-off callers).
  - DB path: resolved by the single canonical resolver
    ``src.utils.paths.trade_journal_db_path()`` (env → $DATA_DIR →
    repo-root); never a CWD-relative basename.
  - All DB ops are best-effort — a failure never blocks a trade.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional
from src.core.coordinator import OrderPackage


_DEFAULT_MIN_QTY = 0.001    # BTC minimum lot size (exchange lot floor)
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

# Round-up-to-one-unit overshoot cap (operator directive 2026-06-24). On a
# whole-SHARE (equity) account the smallest tradeable size is 1 share, so a
# risk-based ideal below 1 is otherwise un-takeable — small accounts can never
# trade higher-priced instruments. When the ONLY reason qty<1 is a small
# per-trade budget, round UP to 1 share IF that single share's stop risk stays
# within this multiple of the per-trade risk budget. Beyond it, still refuse —
# never silently risk more than 1.5x the configured cap. Scoped to the equity
# (whole_units) path; futures whole-contract sizing keeps its strict refuse-
# sub-1 semantics (BL-20260611-001) because a single contract is far chunkier.
_ROUND_UP_BUDGET_MULT = 1.5

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
    ``risk_distance × contract_value_usd``. NOTE: granularity comes from
    ``qty_precision``, not from this factor — futures callers must pass
    ``qty_precision=0`` (``RiskManager.position_size`` enforces this for
    ``market_type: futures``; BL-20260611-001).
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


# Integrations whose order quantity MUST be a whole unit (integer). Alpaca
# bracket orders — the only order class the executor sends for alpaca — reject
# fractional share quantities (``AlpacaClient.place`` floors to
# ``max(1, int(round(qty)))``), so the sizer must produce whole shares: the
# equity analogue of the ``market_type: futures`` whole-contract rule. Without
# this the crypto-oriented default ``qty_precision=3`` produced a fractional
# size (e.g. 9.079 shares) that the broker silently floored to 9 — the journal
# then recorded a qty that was never placed and the risk math was computed on a
# qty that can't exist (BL-20260622-ALPACA-FRACTIONAL-SIZE). Declared as a
# capability set, mirroring ``clients.BROKER_PNL_READER_EXCHANGES`` /
# ``EXCHANGE_MANAGEMENT_CAPS``, rather than a scattered ``== "alpaca"`` check.
WHOLE_UNIT_QTY_EXCHANGES: frozenset = frozenset({"alpaca"})


def requires_whole_unit_qty(exchange: object) -> bool:
    """True when *exchange* requires integer order quantities.

    Pure, never raises. Unknown / falsy exchange → False. The futures
    whole-contract rule stays keyed on ``market_type`` inside
    ``position_size``; this is the orthogonal per-exchange axis (equity
    bracket orders) that ``market_type`` doesn't capture.
    """
    return str(exchange or "").strip().lower() in WHOLE_UNIT_QTY_EXCHANGES


def size_order_from_cfg(
    pkg: OrderPackage,
    account_cfg: dict,
    balance_usdt: float,
) -> float:
    """Build a RiskManager from *account_cfg* and delegate to position_size."""
    rm = RiskManager(account_cfg)
    market_type = str(account_cfg.get("market_type") or "spot").strip().lower()
    # Per-exchange whole-unit constraint (e.g. alpaca bracket orders) is
    # resolved from the FULL account cfg here — the RiskManager itself is built
    # from only the ``risk`` sub-block and never sees the exchange.
    whole_units = requires_whole_unit_qty(account_cfg.get("exchange"))
    return rm.position_size(
        pkg, balance_usdt, market_type=market_type, whole_units=whole_units,
    )


class RiskManager:
    """Per-account risk gate with stateful daily-PnL tracking.

    Parameters (from accounts.yaml ``risk`` section)
    -------------------------------------------------
    max_dd_pct : float
        Maximum drawdown as a fraction of starting equity (e.g., 0.05 = 5 %).
    daily_usd : float
        Maximum allowed daily loss in USD (e.g., 100).
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
        # Percentage-based daily-loss cap (operator-approved 2026-05-28).
        # When > 0, the daily-loss budget is ``daily_loss_pct × equity``
        # rather than the fixed ``daily_usd`` — so the cap scales with the
        # account instead of being a hardcoded USD figure. ``daily_usd``
        # stays as the absolute FALLBACK used only when no equity figure is
        # available (no balance snapshot / cold start). Opted in per-account
        # in config/accounts.yaml; accounts without the field (e.g. the prop
        # account) keep the pure absolute ``daily_usd`` behaviour unchanged.
        # The bybit + IB accounts set it to 0.05 (= ``max_dd_pct``), so the
        # daily realized-loss budget and the intraday equity-drawdown cap use
        # the same 5%-of-equity figure.
        self.daily_loss_pct: float = float(config.get("daily_loss_pct", 0.0) or 0.0)
        self.risk_pct: float = float(config.get("risk_pct", 0.01))
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
        # Single canonical resolver (env → $DATA_DIR → repo-root); never
        # the CWD-relative basename that seeded the stray journals.
        from src.utils.paths import trade_journal_db_path
        return trade_journal_db_path()

    def _ensure_state_table(self, conn: Any) -> None:
        conn.execute(_CREATE_DAILY_RISK_STATE)

    def _load_daily_state(self) -> None:
        """Restore + reconcile today's daily_pnl + daily_high_equity.

        Two sources, reconciled:
          1. The persisted ``daily_risk_state`` row (carries the
             intra-day equity high across restarts).
          2. The canonical journal (authoritative for realized PnL) and
             the balance snapshot (authoritative for current equity).

        Source (2) is why the caps now survive a restart: before this
        change nothing fed ``daily_pnl`` at runtime (record_trade_result
        / update_equity had zero runtime callers), so the table stayed
        empty and the daily-loss / max-drawdown caps reset to 0 on every
        restart. Now the manager rebuilds today's state from the journal
        on init and persists it. No-op when account_id is empty.
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
        # Reconcile against live sources and persist, so a row exists for
        # today even on a fresh boot before the first trade closes.
        self._refresh_daily_from_sources()

    def _recompute_daily_pnl_from_db(self) -> Optional[float]:
        """Sum realized PnL for this account's trades attributed to today.

        Day attribution uses the trade's ``created_at`` (UTC open date) —
        deterministic, join-free, and a close-enough proxy for this
        intraday bot (positions open and close within the same UTC day in
        the overwhelming majority of cases). Read-only; returns None when
        the journal is unavailable so the caller keeps its in-memory value.
        """
        if not self.account_id:
            return None
        try:
            import sqlite3
            today = str(self._today_utc())
            uri = "file:%s?mode=ro" % self._risk_db_path()
            with sqlite3.connect(uri, uri=True, timeout=5) as conn:
                row = conn.execute(
                    "SELECT COALESCE(SUM(pnl), 0.0) FROM trades "
                    "WHERE account_id=? AND status='closed' "
                    "AND pnl IS NOT NULL AND substr(created_at,1,10)=?",
                    (self.account_id, today),
                ).fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0
        except Exception:
            return None  # journal unavailable — keep in-memory value

    def _account_equity_from_snapshot(self) -> Optional[float]:
        """Best-effort current equity from runtime_logs/balance_snapshots.json.

        That file is the same per-account balance the hourly report tracks
        and ``/api/bot/accounts/balances`` serves. Connection-free — never
        opens an exchange socket. Returns None when unavailable.
        """
        if not self.account_id:
            return None
        try:
            import json
            from src.utils.paths import runtime_logs_dir
            p = runtime_logs_dir() / "balance_snapshots.json"
            if not p.exists():
                return None
            raw = json.loads(p.read_text(encoding="utf-8"))
            entry = raw.get(self.account_id) if isinstance(raw, dict) else None
            if isinstance(entry, dict) and entry.get("balance") is not None:
                return float(entry["balance"])
        except Exception:
            return None
        return None

    def _refresh_daily_from_sources(self) -> None:
        """Reconcile in-memory daily state with the journal + balance
        snapshot, then persist if anything changed.

        Best-effort and gated on account_id, so tests and one-off callers
        (account_id="") are unaffected. This is what keeps the caps live
        intra-session and persistent across restarts.
        """
        if not self.account_id:
            return
        changed = False
        pnl = self._recompute_daily_pnl_from_db()
        if pnl is not None and pnl != self.daily_pnl:
            self.daily_pnl = pnl
            changed = True
        eq = self._account_equity_from_snapshot()
        if eq is not None:
            self.current_equity = eq
            if self.daily_high_equity is None or eq > self.daily_high_equity:
                self.daily_high_equity = eq
                changed = True
        if changed:
            self._save_daily_state()

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
        # Reconcile against the journal + balance snapshot on every gate
        # check so the daily-loss / drawdown caps reflect realized PnL and
        # current equity without depending on a runtime caller of
        # record_trade_result()/update_equity() (which had none — the bug
        # that left daily_risk_state empty). Best-effort, no-op when
        # account_id is empty.
        self._refresh_daily_from_sources()

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

    def effective_daily_loss_usd(self, equity: Optional[float] = None) -> float:
        """Return the daily-loss budget in USD for the current account state.

        Percentage mode (``daily_loss_pct > 0`` and a positive *equity*
        figure is available): ``daily_loss_pct × equity`` — the cap scales
        with the account. *equity* defaults to ``self.current_equity`` (the
        last balance-snapshot reading). When neither is available the cap
        falls back to the absolute ``daily_usd`` so there is always a finite
        budget — never an accidentally-infinite one.

        Absolute mode (``daily_loss_pct == 0``, e.g. the prop account):
        always the fixed ``daily_usd`` — behaviour unchanged.
        """
        eq = equity if equity is not None else self.current_equity
        if self.daily_loss_pct > 0 and eq is not None and eq > 0:
            return self.daily_loss_pct * float(eq)
        return self.max_daily_loss_usd

    def is_daily_cap_exhausted(self, equity: Optional[float] = None) -> bool:
        """True when today's realized loss has met/exceeded the daily cap.

        Rolls the day + reconciles from the journal first so the answer
        reflects the same state the sizing gates see. Used by the latching
        daily-cap notification in ``Coordinator.multi_account_execute``.
        """
        self._maybe_roll_daily()
        return self.daily_pnl <= -self.effective_daily_loss_usd(equity)

    def approve(self, order: OrderPackage) -> bool:
        ok, _reason = self.evaluate(order)
        return ok

    def evaluate(self, order: OrderPackage) -> tuple[bool, Optional[str]]:
        if _is_test_order(order):
            return True, None

        if self.dry_run:
            return False, "account_mode_dry_run"

        self._maybe_roll_daily()

        # Percentage-based when daily_loss_pct is set (uses current equity);
        # absolute daily_usd otherwise / on equity-unavailable fallback.
        if self.daily_pnl < -self.effective_daily_loss_usd():
            return False, "DAILY_LOSS_CAP"

        # NOTE: there is intentionally NO position-notional ceiling here.
        # Position size is a pure function of (available balance + margin) and
        # risk-per-trade (SL distance × risk_pct) — see position_size(). An
        # arbitrary max-notional cap (the removed POSITION_SIZE_CAP / pos_size)
        # would gate a correctly risk-sized trade on a number unrelated to the
        # account's actual capacity, so it does not exist (operator directive
        # 2026-06-24). The only sizing constraints are the risk budget, the
        # daily-loss budget, the margin/buying-power ceiling, and the exchange's
        # own minimum lot size.

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
        whole_units: bool = False,
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
        dry-run, or any fetch failure). The basis is ``total_account_usd``
        when supplied (total equity backs the position on UNIFIED
        cross-margin / ``market_type: linear`` accounts), else free
        ``balance_usd``:

            basis = total_account_usd if total_account_usd is not None
                    else balance_usd
            max_qty_by_margin = (basis * effective_leverage *
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
        # No arbitrary minimum-balance floor (the removed ``min_balance_usd``):
        # size is a pure function of available balance+margin and risk-per-trade
        # (operator directive 2026-06-24). The only floor is physics — you can't
        # risk a fraction of zero — so a non-positive balance sizes to 0 (this
        # also guards _size_unbounded's positive-balance requirement).
        if gate_balance <= 0:
            return 0.0

        strategy_risk_pct = float(
            (package.meta or {}).get("strategy_risk_pct") or 1.0
        )
        effective_risk_pct = self.risk_pct * strategy_risk_pct

        # Per-instrument contract value ($/point). 1.0 for crypto (path
        # unchanged); the MES contract multiplier ($5/point) for futures so
        # the USD-loss math below is correct.
        cvu = contract_value_usd_for(getattr(package, "symbol", "") or "")

        # Whole-unit sizing — two orthogonal triggers, same enforcement:
        #   * ``market_type: futures`` — IBKR rejects fractional contract qty,
        #     and the rejection is asynchronous so a fractional order silently
        #     never becomes a position (BL-20260611-001, trade #2531: 3.643 MHG
        #     contracts dispatched, orphaned 30 min later).
        #   * ``whole_units`` (per-exchange, e.g. alpaca) — bracket orders, the
        #     only class the executor sends for alpaca, reject fractional share
        #     qty; the broker floored 9.079 → 9, so the journal recorded a size
        #     that was never placed (BL-20260622-ALPACA-FRACTIONAL-SIZE).
        # Either way: enforce integer granularity here regardless of the
        # account's configured ``qty_precision`` / ``min_qty`` (the crypto
        # defaults — 3dp / 0.001 lot — are what produced the fractional size when
        # an account omitted them). A computed size below 1 whole unit is a
        # per-trade REFUSAL (0.0), never bumped up: the bump would exceed the
        # configured risk cap.
        is_futures = market_type == "futures"
        force_whole = is_futures or bool(whole_units)
        eff_precision = 0 if force_whole else self.qty_precision
        eff_min_qty = 1.0 if force_whole else self.min_qty

        qty = _size_unbounded(
            package,
            risk_pct=effective_risk_pct,
            balance_usdt=balance_usd,
            # min_qty=0.0 for EVERY market type: never bump a sub-floor risk-based
            # size up to the minimum lot. The bump silently realises MORE than the
            # configured per-trade risk budget and — when it equalled a held
            # min-lot — pinned the real-money bybit_2 in a permanent at-target
            # freeze (#3910 Item 3, operator-approved refuse 2026-06-28). The
            # refusal checks below are the only floor.
            min_qty=0.0,
            qty_precision=eff_precision,
            contract_value_usd=cvu,
        )
        if force_whole and qty < eff_min_qty:
            # Round-up-to-one-share (operator directive 2026-06-24), EQUITY only.
            # The risk-based ideal is below 1 whole share. When the only reason
            # is a small per-trade budget, round UP to 1 share IF that share's
            # stop risk is within _ROUND_UP_BUDGET_MULT x the per-trade risk
            # budget — otherwise refuse. The daily-loss-budget and margin/
            # buying-power gates BELOW still apply to the rounded-up share (a
            # share that breaches the daily cap or can't be afforded is
            # re-floored to 0 there), so this relaxes ONLY the per-trade
            # risk-cap refusal, never the hard limits. Futures keep strict
            # refuse-sub-1-contract (BL-20260611-001) — not whole_units.
            if bool(whole_units):
                _rd = abs(package.entry - package.sl)
                _one_unit_risk = eff_min_qty * _rd * cvu
                _risk_budget = balance_usd * effective_risk_pct
                if _risk_budget > 0 and _one_unit_risk <= _ROUND_UP_BUDGET_MULT * _risk_budget:
                    qty = eff_min_qty
                else:
                    return 0.0
            else:
                return 0.0
        elif not force_whole and qty < eff_min_qty:
            # Risk-based size below the exchange's minimum lot -> per-trade
            # REFUSAL (operator-approved 2026-06-28, #3910 Item 3): the account
            # equity is too small to take this trade at the configured risk
            # without over-sizing. The account stays LIVE; this single trade is
            # refused (Prime Directive — a per-trade refusal, never an
            # account-mode flip). Refuse, never bump to the min lot: the bump
            # silently over-risks (it pinned the real-money bybit_2 in a
            # permanent at-target freeze). Crypto/fx refuse STRICTLY — the
            # round-up-to-1 relaxation above is equity-only, where 1 share is the
            # smallest tradeable unit and its stop risk is budget-checked first.
            return 0.0

        # S-026 G3: daily-loss-budget gate. USD loss at SL is
        # qty × risk_distance × contract_value_usd (cvu=1.0 for crypto).
        # The budget is percentage-based (``daily_loss_pct × equity``) when
        # configured, else the absolute ``daily_usd``. ``gate_balance`` (the
        # account equity basis resolved above — total_account_usd when
        # supplied, else balance_usd) is the equity figure; this is what lets
        # a large-balance account (e.g. the bybit demo) stop tripping a
        # hardcoded $100 cap on every signal.
        self._maybe_roll_daily()
        loss_budget_remaining = (
            self.effective_daily_loss_usd(gate_balance) + self.daily_pnl
        )
        if loss_budget_remaining <= 0:
            return 0.0

        risk_distance = abs(package.entry - package.sl)
        max_loss_at_sl = qty * risk_distance * cvu
        if max_loss_at_sl > loss_budget_remaining:
            scaled = loss_budget_remaining / (risk_distance * cvu)
            qty = _floor_to_step(scaled, eff_precision)
            if qty < eff_min_qty:
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
                # Buffer fallback. Prefer total account equity
                # (``total_account_usd``) over free ``balance_usd`` as the
                # margin basis when it was supplied: live accounts are
                # ``market_type: linear`` (Bybit UNIFIED cross-margin), where
                # total equity — not just the free wallet balance — backs the
                # position. Using free balance here re-imposed the very
                # min-balance constraint that ``total_account_usd`` was added
                # to lift (S-052), sizing locked-funds accounts to 0.0.
                _margin_basis = (
                    total_account_usd if total_account_usd is not None else balance_usd
                )
                max_qty_by_margin = (
                    _margin_basis * effective_leverage * _MARGIN_SAFETY_BUFFER
                ) / package.entry
            if qty > max_qty_by_margin:
                # Floor with the EFFECTIVE granularity, not self.qty_precision:
                # on a whole-unit account (alpaca) the margin cap could otherwise
                # shave an already-whole qty down to a FRACTIONAL share (e.g.
                # 3 → 2.3) using the crypto default 3dp precision, re-opening the
                # bracket-rejects-fractional hole BL-20260622-ALPACA-FRACTIONAL-SIZE
                # fixed on the risk-based path. eff_precision/eff_min_qty equal
                # self.qty_precision/self.min_qty for non-whole-unit accounts, so
                # this is a no-op there.
                capped = _floor_to_step(max_qty_by_margin, eff_precision)
                if capped < eff_min_qty:
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
        # Effective daily-loss cap reflects the percentage mode when set
        # (so the dashboard/digest shows the real budget, not the absolute
        # fallback) — uses current_equity, falling back to daily_usd.
        eff_daily_loss = self.effective_daily_loss_usd()
        return {
            "daily_pnl": round(self.daily_pnl, 2),
            "max_daily_loss_usd": round(eff_daily_loss, 2),
            "daily_loss_pct": self.daily_loss_pct,
            "daily_loss_usd_floor": self.max_daily_loss_usd,
            "max_dd_pct": self.max_dd_pct,
            "daily_loss_remaining": round(
                eff_daily_loss + self.daily_pnl, 2
            ),
            "current_equity": (
                round(self.current_equity, 2) if self.current_equity is not None else None
            ),
            "daily_high_equity": (
                round(self.daily_high_equity, 2) if self.daily_high_equity is not None else None
            ),
            "intraday_drawdown_pct": round(dd, 4) if dd is not None else None,
            "halted": (
                self.daily_pnl < -eff_daily_loss
                or (dd is not None and dd >= self.max_dd_pct)
            ),
        }
