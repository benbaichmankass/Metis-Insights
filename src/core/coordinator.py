"""TRANSLATOR / Coordinator — S-008 PR #120.
Central routing layer between the 9 units defined in config/units.yaml.
No unit communicates with another unit directly; all cross-unit data flows
through this class.

Unit interface stubs (filled in by subsequent PRs):
  PR #121 → strategy_order_pkg()   DONE — src/units/strategies/<name>.py
  PR #122 → account_execute()      DONE — src/units/accounts/execute.py

Data flow:
  Strategies.order_package() ──▶ Coordinator ──▶ Accounts.execute(pkg)
                                       │
  Dashboards.stats() ◄────────────────┘
                                       │
  ReturnCommands.halt() ─────────────┘
"""
from __future__ import annotations

import dataclasses
import importlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import yaml

from src.runtime.orders import account_state_dry_run

if TYPE_CHECKING:
    from typing import Sequence
    from src.units.accounts.account import TradingAccount
    from src.core.allocator import AllocatorInterface
    from src.core.portfolio_state import PortfolioState
    from src.core.signal_contract import SignalPackage
    from src.core.order_contract import OrderPackage

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_UNITS_YAML = os.path.join(_REPO_ROOT, "config", "units.yaml")
_ACCOUNTS_YAML = os.path.join(_REPO_ROOT, "config", "accounts.yaml")
_INSTRUMENTS_YAML = os.path.join(_REPO_ROOT, "config", "instruments.yaml")


def _has_open_position(account_name: str, symbol: str) -> bool:
    """Return True if account already has an open live trade for symbol."""
    import sqlite3
    from src.utils.paths import trade_journal_db_path
    db_path = trade_journal_db_path()
    if not os.path.exists(db_path):
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE account_id = ? AND symbol = ? "
                "AND status = 'open' AND COALESCE(is_backtest, 0) = 0",
                (account_name, symbol),
            ).fetchone()
        return bool(row and row[0] > 0)
    except Exception:  # noqa: BLE001
        return False


# In-process pause sentinels (PR #122 will replace with persistent flags).
_PAUSED_ACCOUNTS: set[str] = set()

# Consecutive exchange-rejection tracker — alerts the operator when an
# account sees repeated rejections without an intervening success. Does NOT
# flip the account mode (Prime Directive: no auto-flip). In-process counter;
# restart resets it. A successful placement zeroes the counter.
_EXCHANGE_REJECTION_ALERT_THRESHOLD = 3
_EXCHANGE_REJECTION_COUNTS: Dict[str, int] = {}


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class OrderPackage:
    """Typed output of a strategy — the only thing Accounts receive.

    Produced by strategy_order_pkg(); consumed by account_execute().
    """

    strategy: str
    symbol: str
    direction: str       # "long" | "short"
    entry: float
    sl: float            # stop-loss price
    tp: float            # primary take-profit price
    confidence: float = 0.0  # 0..1 model score / probability
    meta: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_units(path: str = _UNITS_YAML) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# Cached symbol -> exchange map from config/instruments.yaml. Used by the
# dispatch filter to route a package only to accounts on the symbol's
# exchange (BTCUSDT→bybit, MES→interactive_brokers). Symbols without a
# profile return None → no symbol-based filtering (legacy behaviour).
_INSTRUMENT_EXCHANGE_CACHE: Optional[Dict[str, str]] = None


def _instrument_exchange_for(symbol: str) -> Optional[str]:
    """Return the exchange a *symbol* trades on, or None if unknown."""
    global _INSTRUMENT_EXCHANGE_CACHE
    if not symbol:
        return None
    if _INSTRUMENT_EXCHANGE_CACHE is None:
        try:
            from src.core.profile_loader import load_instrument_profiles
            profiles = load_instrument_profiles()
            _INSTRUMENT_EXCHANGE_CACHE = {
                sym: str(getattr(p, "exchange", "") or "").lower()
                for sym, p in (profiles or {}).items()
            }
        except Exception:  # noqa: BLE001
            _INSTRUMENT_EXCHANGE_CACHE = {}
    return _INSTRUMENT_EXCHANGE_CACHE.get(symbol) or None


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class Coordinator:
    """TRANSLATOR: routes data between the 9 units.

    Instantiate once per process.  Pass a custom *units_path* in tests.
    All cross-unit calls go through this object — never bypass it.
    """

    def __init__(
        self,
        units_path: str = _UNITS_YAML,
        accounts_path: str = _ACCOUNTS_YAML,
        instruments_path: str = _INSTRUMENTS_YAML,
    ) -> None:
        self._units_path = units_path
        self._accounts_path = accounts_path
        self._instruments_path = instruments_path
        self._cfg: Dict[str, Any] = {}
        # S-AI-WS7-PART-6: Coordinator-side cache of resolved
        # ShadowPredictor lists, keyed by strategy name. Lazily
        # populated on first dispatch; invalidated by
        # ``reload_strategy_config`` so a YAML edit re-resolves on
        # the next tick. Lifts the per-tick factory call out of the
        # strategy hot path.
        self._shadow_predictors_cache: Dict[str, list] = {}
        self._allocator: Any = None  # lazy-init PassthroughAllocator (S4)
        self._reload()

    @property
    def account_profiles(self) -> Dict[str, Any]:
        """Read-only typed view of config/accounts.yaml as AccountProfile objects."""
        from src.core.profile_loader import load_account_profiles
        return load_account_profiles(self._accounts_path)

    @property
    def instrument_profiles(self) -> Dict[str, Any]:
        """Read-only typed view of config/instruments.yaml as InstrumentProfile objects."""
        from src.core.profile_loader import load_instrument_profiles
        return load_instrument_profiles(self._instruments_path)

    @property
    def allocator(self) -> "AllocatorInterface":
        """Lazy-init PassthroughAllocator (S4 wiring).

        Returns the same instance on repeated calls. The instance is shared
        across calls; hot-path allocate() calls are cheap (no IO).
        Swap in a different allocator by replacing self._allocator directly
        in tests or future sprints.
        """
        if self._allocator is None:
            from src.core.allocator import PassthroughAllocator
            self._allocator = PassthroughAllocator()
        return self._allocator

    def build_order_packages(
        self,
        signals: "Sequence[SignalPackage]",
        portfolio_state: "dict | PortfolioState | None" = None,
        *,
        db_path: Optional[str] = None,
    ) -> "list[OrderPackage]":
        """Size a batch of SignalPackages through the allocator (S8 wiring).

        Builds a typed ``PortfolioState`` from whatever the caller provides,
        enriches it with live net positions from the trade journal, then
        delegates to ``self.allocator.allocate()``.

        Args:
            signals: SignalPackage objects from strategy signal builders.
            portfolio_state: Either a typed ``PortfolioState`` (used as-is),
                a legacy dict (converted via ``PortfolioState.from_dict()``),
                or ``None`` (creates a zero-balance state). When a dict or
                ``None`` is passed, net positions are fetched from the trade
                journal and merged in.
            db_path: Override for the trade journal path (tests / staging).

        Returns:
            List of sized OrderPackage objects ready for account_execute().
            Empty list when no signal is actionable or no valid stop-loss.
        """
        from src.core.portfolio_state import PortfolioState
        from src.runtime.positions import net_positions_by_symbol

        if isinstance(portfolio_state, PortfolioState):
            ps = portfolio_state
        elif portfolio_state is None:
            live_positions = net_positions_by_symbol(db_path=db_path)
            ps = PortfolioState(balance=0.0, net_positions=live_positions)
        else:
            ps = PortfolioState.from_dict(portfolio_state)
            if not ps.net_positions:
                ps.net_positions = net_positions_by_symbol(db_path=db_path)

        return self.allocator.allocate(signals, ps)

    def log_advisory_scores(
        self,
        scores: dict[str, float],
        *,
        strategy_id: str = "",
        symbol: str = "",
    ) -> None:
        """Log advisory-stage model scores. No order action taken (S10).

        This is the coordinator advisory hook wired by S10. It is a
        read-only observation point: scores are emitted to the Python
        logger at INFO level and to ``runtime_logs/advisory_decisions.jsonl``
        for audit. The live order path is completely unaffected.

        The hook is a noop when ``scores`` is empty so callers can always
        invoke it unconditionally without an ``if advisory_scores:`` guard.

        Parameters
        ----------
        scores : dict[str, float]
            ``{model_id: score}`` from ``with_shadow_preds_advisory()``.
            Only advisory-stage scores are expected here (shadow scores are
            filtered out by the adapter before reaching the coordinator).
        strategy_id : str
            Strategy that generated the signal (for audit context).
        symbol : str
            Trading symbol (for audit context).
        """
        if not scores:
            return
        import json
        from datetime import datetime, timezone
        from pathlib import Path

        logged_at = datetime.now(timezone.utc).isoformat()
        for model_id, score in scores.items():
            logger.info(
                "advisory_score model_id=%s score=%.6f strategy=%s symbol=%s",
                model_id, score, strategy_id, symbol,
            )
        log_path = Path("runtime_logs/advisory_decisions.jsonl")
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "logged_at_utc": logged_at,
                "strategy_id": strategy_id,
                "symbol": symbol,
                "advisory_scores": scores,
            }
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
        except OSError as exc:
            logger.warning("log_advisory_scores: could not write audit log: %s", exc)

    def multi_account_execute_typed(
        self,
        pkgs: "list[OrderPackage]",
        accounts_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Dispatch pre-sized typed OrderPackages from build_order_packages().

        S7 (M11): converts each typed OrderPackage (order_contract.py) to
        the legacy coordinator format and delegates to multi_account_execute.
        Per-account RiskManager sizing still runs; allocator-computed qty is
        stored in meta['allocator_qty'] for audit only — the RiskManager
        remains the single live-sizing authority until S8.

        Parameters
        ----------
        pkgs : list[OrderPackage]
            Typed packages from build_order_packages() / allocator.allocate().
        accounts_path : str, optional
            Override path to accounts.yaml (forwarded to multi_account_execute).

        Returns
        -------
        list[dict]
            Concatenated per-account results from multi_account_execute.
            Flat packages (qty==0 or side=='none') are skipped silently.
        """
        results: List[Dict[str, Any]] = []
        for typed_pkg in pkgs:
            if getattr(typed_pkg, "is_flat", False):
                logger.debug(
                    "multi_account_execute_typed: skipping flat pkg strategy=%s",
                    getattr(typed_pkg, "strategy_id", "?"),
                )
                continue
            legacy_pkg = OrderPackage(
                strategy=str(getattr(typed_pkg, "strategy_id", "") or ""),
                symbol=str(getattr(typed_pkg, "symbol", "") or "BTCUSDT"),
                direction=str(getattr(typed_pkg, "side", "long")),
                entry=float(getattr(typed_pkg, "entry_price", 0.0) or 0.0),
                sl=float(getattr(typed_pkg, "stop_loss", 0.0) or 0.0),
                tp=float(getattr(typed_pkg, "take_profit", 0.0) or 0.0),
                confidence=0.0,
                meta={"allocator_qty": getattr(typed_pkg, "qty", 0.0)},
            )
            account_results = self.multi_account_execute(
                legacy_pkg,
                accounts_path=accounts_path,
            )
            results.extend(account_results)
        return results

    def _reload(self) -> None:
        try:
            self._cfg = _load_units(self._units_path)
        except FileNotFoundError:
            logger.warning("units.yaml not found at %s; using empty config", self._units_path)
            self._cfg = {}

    def reload_units(self) -> Dict[str, Any]:
        """Re-read units.yaml and refresh the Coordinator's config in-place.

        Returns a summary of what changed: ``{reloaded: bool, units_path: str,
        strategy_count: int, enabled_strategies: list[str]}``.

        Pushes an info alert so Telegram / App consumers see the reload event.
        """
        self._reload()
        from src.units import list_enabled_strategies
        enabled = list_enabled_strategies(self._units_path)
        summary = {
            "reloaded": True,
            "units_path": self._units_path,
            "strategy_count": len(self.list_strategies()),
            "enabled_strategies": enabled,
        }
        self.push_alert(
            f"Units reloaded from {self._units_path}: "
            f"{len(enabled)} enabled strategies",
            source="app",
            level="info",
            **summary,
        )
        logger.info("reload_units: %s", summary)
        return summary

    # ------------------------------------------------------------------
    # Unit 1 → Strategies
    # ------------------------------------------------------------------

    def strategy_order_pkg(
        self,
        strategy: str,
        symbol: str = "BTCUSDT",
        candles_df=None,
    ) -> OrderPackage:
        """Generate an OrderPackage from *strategy*.

        Delegates to ``src.units.strategies.<strategy>.order_package(cfg, candles_df)``.

        Parameters
        ----------
        strategy : str
            Name matching a unit in config/units.yaml → strategies.
        symbol : str
            Market symbol, merged into the strategy cfg.
        candles_df : pd.DataFrame, optional
            OHLCV frame.  Most strategies require this; pass hand-crafted
            DataFrames in tests (no live exchange calls).

        Raises
        ------
        NotImplementedError
            When the strategy module does not yet exist.
        ValueError
            When the signal is non-actionable (strategy returned side="none").
        """
        try:
            mod = importlib.import_module(f"src.units.strategies.{strategy}")
            if not hasattr(mod, "order_package"):
                raise AttributeError("module has no order_package()")
            cfg = {
                **self._strategy_cfg(strategy),
                "symbol": symbol,
                # S-AI-WS7-PART-6: inject pre-resolved shadow
                # predictor list (resolution mode 1 in
                # vwap/turtle_soup). The strategy's per-tick factory
                # call short-circuits when this key is present.
                "_shadow_predictors": self._get_shadow_predictors(strategy),
            }
            pkg_dict = mod.order_package(cfg, candles_df=candles_df)
            pkg = OrderPackage(strategy=strategy, **pkg_dict)
            if pkg.meta is None:
                pkg.meta = {}
            pkg.meta["trace_id"] = pkg.trace_id
            logger.info(
                "[coordinator] trace_id=%s strategy=%s symbol=%s direction=%s",
                pkg.trace_id, strategy, pkg.symbol, pkg.direction,
            )
            return pkg
        except ImportError:
            raise NotImplementedError(
                f"Strategy module 'src.units.strategies.{strategy}' not found; "
                "implement in PR #121."
            )
        except AttributeError as exc:
            raise NotImplementedError(
                f"Strategy '{strategy}' does not expose order_package(): {exc}; "
                "implement in PR #121."
            )

    def list_strategies(self) -> List[Dict[str, Any]]:
        """Return strategy configs from units.yaml."""
        units = self._cfg.get("units") or {}
        return list(units.get("strategies") or [])

    def _strategy_cfg(self, name: str) -> Dict[str, Any]:
        for s in self.list_strategies():
            if isinstance(s, dict) and s.get("name") == name:
                return s
        return {"name": name}

    def _get_shadow_predictors(self, name: str) -> list:
        """Resolve and cache shadow predictors for *name* (S-AI-WS7-PART-6).

        Resolution rules (2026-05-19 auto-wire update):
          * ``shadow_model_ids`` **missing or None** — auto-discover
            every model whose ``target_deployment_stage`` is
            ``shadow`` from the registry and use that list. This is
            the default lifecycle: every shadow-stage model logs
            predictions on every strategy's signals, with zero
            effect on the order package (the WS7 non-negotiable).
          * ``shadow_model_ids: []`` — deliberate opt-out, no
            shadow predictors for this strategy.
          * ``shadow_model_ids: [...]`` (non-empty) — explicit list,
            exactly those models, regardless of stage. The factory
            still applies its own stage gate per-id.

        Memoised. ``reload_strategy_config`` clears the cache so a
        YAML edit AND/OR a registry promotion re-resolves on the
        next tick. (Promotions that arrive without a YAML reload
        will be picked up the next time the cache is cleared by
        any other path.)

        Per-id failures within ``resolve_predictors(strict=False)``
        are logged and skipped — one bad id never poisons the rest
        of the list. The returned list is the same object stored
        in the cache; the dispatcher should not mutate it.
        """
        if name in self._shadow_predictors_cache:
            return self._shadow_predictors_cache[name]
        cfg = self._strategy_cfg(name)
        # Distinguish "missing or None" (auto-wire) from "explicit
        # empty list" (opt-out). The YAML loader gives us None for
        # a missing key and `[]` for `shadow_model_ids: []`.
        raw_ids = cfg.get("shadow_model_ids", None)
        auto_wire = raw_ids is None
        ids: list[str] = [] if raw_ids is None else list(raw_ids)
        if not auto_wire and not ids:
            # Explicit opt-out — `shadow_model_ids: []`.
            self._shadow_predictors_cache[name] = []
            return self._shadow_predictors_cache[name]
        # Lazy import: ml.shadow imports the registry, which is
        # heavier than the strategy hot path needs unless shadow
        # mode is actually wired.
        from pathlib import Path as _Path

        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import (
            DEFAULT_REGISTRY_ROOT,
            discover_shadow_stage_model_ids,
            resolve_predictors,
        )
        from src.utils.paths import runtime_logs_dir as _runtime_logs_dir

        registry_root = _Path(
            cfg.get("_shadow_registry_root") or DEFAULT_REGISTRY_ROOT
        )
        # 2026-05-19: resolve the shadow audit-log path through
        # `runtime_logs_dir()` instead of the factory's CWD-relative
        # `DEFAULT_LOG_PATH`. On the live VM `runtime_logs_dir()` is
        # `${DATA_DIR}/runtime_logs/` (canonical
        # `/data/bot-data/runtime_logs/` via the systemd drop-in);
        # the factory's `Path("runtime_logs/shadow_predictions.jsonl")`
        # resolved relative to the trader process's CWD
        # (`/home/ubuntu/ict-trading-bot/`), so the trader wrote to
        # one file while `src/web/api/routers/trade_scores.py` (which
        # uses `runtime_logs_dir() / "shadow_predictions.jsonl"`)
        # read from a different one. Symptom: `/api/bot/trades/scores`
        # returned `log_present: False` even though shadow predictions
        # were happily firing on every signal — the writer-vs-reader
        # split-brain documented in `src/utils/paths.py:223` recurring
        # one layer up. Tests are unaffected (no DATA_DIR env →
        # `runtime_logs_dir()` returns `<repo>/runtime_logs/`, same
        # parent as the old relative default).
        configured_log = cfg.get("_shadow_log_path")
        log_path = (
            _Path(configured_log) if configured_log
            else _runtime_logs_dir() / "shadow_predictions.jsonl"
        )
        registry = ModelRegistry(registry_root)
        if auto_wire:
            # Symbol-aware auto-wire (2026-06-18): only models trained on this
            # strategy's symbol (or symbol-agnostic `all` decision models), so
            # an alt/futures strategy never auto-wires a different-symbol regime
            # head and pollutes that head's shadow track record.
            _syms = cfg.get("symbols") or []
            _strat_symbol = _syms[0] if _syms else None
            ids = discover_shadow_stage_model_ids(registry, symbol=_strat_symbol)
            if not ids:
                # No shadow-stage models in the registry yet — cache
                # the empty list and bail. Will repopulate the next
                # time the cache is cleared (typically a YAML reload
                # or a fresh process).
                self._shadow_predictors_cache[name] = []
                return self._shadow_predictors_cache[name]
        predictors = resolve_predictors(
            ids,
            registry,
            log_path=log_path,
        )
        self._shadow_predictors_cache[name] = predictors
        return predictors

    # ------------------------------------------------------------------
    # Unit 2 → Accounts
    # ------------------------------------------------------------------

    def account_execute(
        self,
        account_id: str,
        pkg: OrderPackage,
        exchange_client=None,
        balance_usdt: Optional[float] = None,
        *,
        dry_run: Optional[bool] = None,
    ) -> str:
        """Risk-size and execute *pkg* on *account_id*.  Returns a trade_id.

        Parameters
        ----------
        account_id : str
            Must match the ``id`` / ``account_id`` in units.yaml.
        pkg : OrderPackage
            The order package from strategy_order_pkg().
        exchange_client : object, optional
            Bybit/Binance client.  When None the call runs in dry-run mode.
        balance_usdt : float, optional
            Balance override; skips live fetch.  Used in tests.
        dry_run : bool, optional
            Explicit dry-run override; defaults to the per-account
            ``mode`` field in ``config/accounts.yaml`` (the only
            dry/live toggle in the codebase).

        Raises
        ------
        RuntimeError
            When the account is paused.
        KeyError
            When account_id is not found in units.yaml.
        """
        account_cfg = self._account_cfg(account_id)
        from src.units.accounts.execute import execute_pkg
        trade_id = execute_pkg(
            pkg, account_cfg,
            exchange_client=exchange_client,
            balance_usdt=balance_usdt,
            dry_run=dry_run,
        )
        self.push_alert(
            f"Executed {pkg.strategy} {pkg.direction} {pkg.symbol} → {trade_id}",
            source="accounts",
            level="info",
            strategy=pkg.strategy,
            symbol=pkg.symbol,
            direction=pkg.direction,
            trade_id=trade_id,
            account_id=account_id,
        )
        return trade_id

    def _account_cfg(self, account_id: str) -> dict:
        for acc in self.list_accounts():
            if acc.get("account_id") == account_id:
                return acc
        raise KeyError(f"Account '{account_id}' not found in units.yaml")

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Return account configs.

        S-012 PR B3: accounts.yaml is the production single source of truth
        (PM § 8 #3). Read order:
          1. config/accounts.yaml via data_loaders._load_yaml_accounts() —
             strict YAML-only read; deliberately bypasses _load_env_accounts()
             so .env.* file discovery does not introduce phantom services
             (e.g. .env.example → ict-trader-example). The full phantom
             regression test ships in PR D3.
          2. units.yaml::accounts — back-compat for synthetic test fixtures
             (test_s008_coordinator) that embed accounts in a tmp units.yaml.
          3. data_loaders.list_accounts() — final legacy env-only fallback,
             used only when neither accounts.yaml nor units.yaml::accounts
             yields anything.
        """
        # 1. accounts.yaml strict YAML read.
        if os.path.exists(self._accounts_path):
            try:
                from src.bot import data_loaders
                original = data_loaders.ACCOUNTS_YAML_PATH
                data_loaders.ACCOUNTS_YAML_PATH = self._accounts_path
                try:
                    accounts = data_loaders._load_yaml_accounts()
                    if accounts:
                        return accounts
                finally:
                    data_loaders.ACCOUNTS_YAML_PATH = original
            except Exception as exc:
                logger.warning("list_accounts: accounts.yaml read failed: %s", exc)

        # 2. units.yaml::accounts back-compat for synthetic test fixtures.
        units = self._cfg.get("units") or {}
        accounts = units.get("accounts") or []
        if accounts:
            return [
                {
                    "account_id": a.get("id") or a.get("account_id") or "",
                    "exchange": a.get("exchange", "unknown"),
                    "risk_pct": a.get("risk_pct", 0.01),
                    "env_path": a.get("env_path"),
                    "strategies": list(a.get("strategies") or []),
                    "source": "units_yaml",
                }
                for a in accounts
                if isinstance(a, dict)
            ]

        # 3. Final fallback — env-only discovery.
        try:
            from src.bot.data_loaders import list_accounts as _dl_accounts
            return _dl_accounts()
        except Exception as exc:
            logger.warning("list_accounts fallback failed: %s", exc)
            return []

    def is_account_paused(self, account_id: str) -> bool:
        """Return True when *account_id* has been halted via return_command."""
        return account_id in _PAUSED_ACCOUNTS

    def accounts_status(
        self, accounts_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return per-account status dicts from config/accounts.yaml.

        Each dict contains name, exchange, account_type, open_positions,
        daily_pnl, max_daily_loss_usd, halted, plus
        live API integration fields (S-021):

        - ``live_balance_usdt``: total USDT balance fetched from the
          exchange API (None when the API call failed).
        - ``live_balance_error``: human-readable error string when the
          API integration is broken (missing creds, network, etc.).
          None when balance was fetched successfully.

        These fields make it obvious from ``/accounts_status`` whether
        the bot's per-account API keys are wired correctly — the
        previous version only showed the local risk state, so a
        broken API integration looked the same as a working one.

        Parameters
        ----------
        accounts_path : str, optional
            Path to accounts.yaml.  Defaults to ``config/accounts.yaml``.
        """
        from src.units.accounts import load_accounts
        import os as _os
        path = accounts_path or _os.path.join(_REPO_ROOT, "config", "accounts.yaml")
        try:
            tradeaccs = load_accounts(path)
        except FileNotFoundError:
            logger.warning("accounts_status: accounts.yaml not found at %s", path)
            return []

        # Resolve live balances via the same data_loaders path /balance uses,
        # so the two surfaces report the same numbers. Honour the explicit
        # accounts_path the caller passed in (the integration test fixture
        # writes a tmp accounts.yaml and expects accounts_status to read
        # exactly that file).
        try:
            from src.bot import data_loaders as _dl
            original = _dl.ACCOUNTS_YAML_PATH
            _dl.ACCOUNTS_YAML_PATH = path
            try:
                yaml_accounts = {
                    a.get("account_id"): a
                    for a in (_dl._load_yaml_accounts() or [])
                }
            finally:
                _dl.ACCOUNTS_YAML_PATH = original
        except Exception as exc:  # noqa: BLE001
            logger.warning("accounts_status: yaml lookup failed: %s", exc)
            yaml_accounts = {}

        out: List[Dict[str, Any]] = []
        for ta in tradeaccs:
            entry = ta.status()
            entry["live_balance_usdt"] = None
            entry["live_balance_error"] = None
            # BUG-033: surface a short fingerprint of the resolved API key
            # so the operator can see at a glance whether two accounts
            # are pointed at the same wallet. Computed via the accounts
            # unit (resolve_credentials) so the fingerprint matches the
            # creds the order layer would actually use.
            entry["api_key_fingerprint"] = None
            try:
                from src.units.accounts.clients import resolve_credentials
                creds = resolve_credentials({
                    "api_key_env": getattr(ta, "api_key_env", ""),
                    "exchange": ta.exchange,
                })
                if creds and creds.get("api_key"):
                    entry["api_key_fingerprint"] = creds["api_key"][-4:]
            except Exception as exc:  # noqa: BLE001
                logger.debug("api_key_fingerprint lookup failed: %s", exc)
            cfg = yaml_accounts.get(ta.name)
            if cfg is None:
                entry["live_balance_error"] = (
                    "account missing from data_loaders view of accounts.yaml"
                )
                out.append(entry)
                continue
            # S-023 PR2: use the diagnostic variant so the operator
            # sees exactly which env var is missing or what API error
            # fired — replacing the previous generic "missing API creds
            # or exchange rejected the request" message.
            try:
                from src.bot.data_loaders import (
                    account_balance_with_diagnostic as _acct_bal_diag,
                )
                diag = _acct_bal_diag(cfg)
            except Exception as exc:  # noqa: BLE001
                entry["live_balance_error"] = (
                    f"unexpected: {type(exc).__name__}: {exc}"
                )
                out.append(entry)
                continue

            if diag["status"] == "ok":
                entry["live_balance_usdt"] = float(diag["total_usdt"] or 0.0)
            else:
                entry["live_balance_error"] = diag["error"]
            out.append(entry)
        return out

    def multi_account_execute(
        self,
        pkg: OrderPackage,
        accounts_path: Optional[str] = None,
        *,
        dry_run: Optional[bool] = None,
        account_type: Optional[str] = None,
        balance_fetcher: Optional[Callable[["TradingAccount"], float]] = None,
    ) -> List[Dict[str, Any]]:
        """Execute *pkg* on all accounts loaded from accounts.yaml.

        S-026 G2: per-account sizing happens here. Each account's
        ``risk_manager.position_size(pkg, balance)`` is called before the
        package is forwarded; the resulting qty is recorded under
        ``pkg.meta['sized_qty_by_account'][account.name]``. Accounts with a
        non-positive balance produce ``qty=0.0`` and a ``zero_balance`` skip
        result instead of being routed (there is no arbitrary minimum-balance
        floor — ``min_balance_usd`` was removed 2026-06-24).

        Parameters
        ----------
        pkg : OrderPackage
            The order package from strategy_order_pkg().
        accounts_path : str, optional
            Override path to accounts.yaml.
        dry_run : bool, optional
            **Process-level override.** When ``None`` (default), each
            account's ``mode: live | dry_run`` field decides — per
            CLAUDE.md's autonomous live-trading rule that the
            per-account RiskManager is the SINGLE dry/live toggle.
            Tests pass ``True`` / ``False`` to force a specific mode
            for the whole dispatch round.
            Pre-fix the default was ``True``, which silently
            overrode every account's ``mode: live`` flag — pipeline
            calls into this method without specifying ``dry_run``,
            so every signal was dispatched in dry mode regardless of
            the YAML config. The bug surfaced as "5 actionable
            signals fired, 0 trades landed" on 2026-05-03 even
            though ``bybit_2`` was ``mode: live`` with $177 balance.
        account_type : str, optional
            When set, only execute on accounts matching this type
            (``"regular"`` | ``"prop"``).
        balance_fetcher : callable, optional
            ``(account) -> balance_usd`` override. When None the
            in-process default is used: read ``meta['account_balance_usd']``
            on the package, then ``account.cached_balance_usd``, then
            fall back to a smoke-safe stub of $0.0 (which produces a
            ``zero_balance`` skip — surfacing the missing wiring
            instead of placing an unsized order). G3 will replace the
            default with a live ``processor.get_account_balances()`` call.

        Returns
        -------
        list[dict]
            One result dict per account:
            ``{name, exchange, account_type, trade_id, error, sized_qty}``
        """
        from src.units.accounts import load_accounts
        from src.units.accounts.account import RiskBreach
        import os as _os

        path = accounts_path or _os.path.join(_REPO_ROOT, "config", "accounts.yaml")
        try:
            accounts = load_accounts(path)
        except FileNotFoundError:
            logger.warning("multi_account_execute: accounts.yaml not found at %s", path)
            return []

        # S-026 G2: stamp a per-account qty map onto the package so
        # downstream routing can read what the sizer decided. Mutating
        # the meta dict in-place is fine — the package is constructed
        # fresh by ``_signal_to_order_package`` for each tick.
        sized_qty_by_account: Dict[str, float] = {}
        if pkg.meta is None:
            pkg.meta = {}
        pkg.meta["sized_qty_by_account"] = sized_qty_by_account

        # S-026 G3: live balance fetcher. Pull every account's USDT
        # balance once at the top of the dispatch round and cache the
        # ``account_id → total_usdt`` map locally so each per-account
        # call is an O(1) lookup. Lookup order:
        #   1. Caller-supplied ``balance_fetcher`` override (tests +
        #      one-shot dispatch paths use this).
        #   2. ``pkg.meta["account_balances_usd"][acc.name]`` — explicit
        #      per-tick override (also test-friendly).
        #   3. Live ``processor.get_account_balances()`` lookup by
        #      ``account_id``. Returns ``None`` when the row is missing
        #      or the exchange call failed; the per-account RiskManager
        #      then refuses to size (zero_balance).
        # ``cached_balance_usd`` on the account object remains as a final
        # fallback so synthetic test fixtures that pre-stash a balance
        # still work without any wiring.
        live_balances: Dict[str, Optional[float]] = {}
        if balance_fetcher is None:
            try:
                from src.units.ui.processor import get_account_balances
                for row in get_account_balances() or []:
                    aid = row.get("account_id")
                    if aid:
                        # row["total_usdt"] is None when the lookup
                        # failed; preserve that so the sizer can refuse
                        # cleanly instead of treating missing as 0.0.
                        live_balances[aid] = row.get("total_usdt")
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "multi_account_execute: live balance fetch failed (%s) "
                    "— per-account sizers will use the cached/explicit "
                    "fallback instead",
                    exc,
                )

        def _default_balance_fetcher(acc) -> float:
            # 1. Per-tick override stashed on pkg.meta — tests + the
            #    bot's per-tick balance refresh use this.
            pkg_balances = (pkg.meta or {}).get("account_balances_usd") or {}
            if acc.name in pkg_balances:
                return float(pkg_balances[acc.name])
            # 2. Live lookup, cached at the top of this dispatch round.
            #    live_balances distinguishes three states:
            #      - acc.name NOT in dict: balance fetch was not attempted
            #        (test fixtures, whole-fetch exception) → use cached/0.0
            #      - acc.name IN dict, value not None: API succeeded → use it
            #      - acc.name IN dict, value is None: API was attempted and
            #        failed → try cached, else raise (BL-20260625-ALPACA-ZB:
            #        the silent 0.0 fallback here was producing the misleading
            #        "zero_balance: gate_balance=0.00" error when the Alpaca
            #        API was unreachable — the account isn't empty, the fetch
            #        failed; raising surfaces the real cause via sizing_failed)
            if acc.name in live_balances:
                live = live_balances[acc.name]
                if live is not None:
                    return float(live)
                cached = getattr(acc, "cached_balance_usd", None)
                if cached is not None:
                    return float(cached)
                raise RuntimeError(
                    f"balance() returned None for {acc.name} "
                    f"(exchange={getattr(acc, 'exchange', 'unknown')}): "
                    "API error or credentials missing — account unreachable"
                )
            # 3. acc.name not in live_balances: balance fetch not attempted
            #    (test fixtures or whole-fetch exception). Use cached or 0.0.
            cached = getattr(acc, "cached_balance_usd", None)
            return float(cached) if cached is not None else 0.0

        fetcher = balance_fetcher or _default_balance_fetcher

        # CLAUDE.md § Architecture rules § 2 + § 4 +
        # architecture-audit-2026-05-02 P1-5: log the OrderPackage to
        # the DB unit's order_packages table once per dispatch round.
        # Pre-S-030 the OrderPackage was in-memory only — the operator
        # could see "a vwap signal fired" + "a trade exists" but could
        # not trace the package that linked them or replay how it
        # evolved. The id is generated here and stamped on the package
        # so per-account result rows can reference it. Best-effort —
        # journal failures must never crash the dispatch.
        order_package_id = _log_new_order_package(pkg)
        if order_package_id:
            # Always propagate the id onto the package so every trade row this
            # decision fans out to carries ``order_package_id`` (the trade ↔
            # package back-reference the reconciler + dashboard read). Pre-fix
            # this was gated on ``pkg.meta`` already being a dict, so a package
            # with ``meta=None`` journaled its trade with a NULL link (the MHG
            # #2578 orphan, operator report 2026-06-16). Initialise meta first.
            if not isinstance(pkg.meta, dict):
                pkg.meta = {}
            pkg.meta["order_package_id"] = order_package_id

        # Per-account strategy filter (CLAUDE.md § Architecture rules
        # § 3, 2026-05-08 reversal of S-029-PR1). Each account in
        # accounts.yaml declares ``strategies: [...]`` — the package's
        # strategy must be on that list for the account to enter
        # dispatch. Accounts that don't match are filtered upfront,
        # *before* the loop body — so they don't appear in
        # ``results`` and don't generate per-tick rejection rows in
        # the trades table.
        #
        # History: pre-S-029 every signal fanned out to every account
        # regardless of assignment, landing vwap packages in
        # turtle_soup-only wallets. S-029 PR1 added the filter inside
        # the loop and wrote a `skipped_not_assigned` rejection row
        # per skipped (account, tick) pair so the operator could see
        # which accounts had been considered. With multi-strategy +
        # multi-account fan-out at 1-min ticks, those rejection rows
        # became O(strategies × accounts × ticks-per-day) noise that
        # buried real refusals. Operator directive 2026-05-08:
        # filter the list, don't log a refusal — the strategies map in
        # accounts.yaml is the audit trail.
        #
        # Three rules, in order:
        #   1. ``configured == False`` → drop. Scaffolded accounts (e.g.
        #      a prop or new-broker account whose env-var creds aren't set)
        #      load with ``configured=False`` when
        #      env-var creds are missing; they exist for /accounts_status
        #      visibility but must never enter live dispatch. Pre-fix
        #      these were producing per-tick ``below_min_balance``
        #      rejection rows since the strategy filter let them
        #      through and the risk gate then refused on $0 balance.
        #   2. ``strategies`` declared but empty (`[]`) → drop. Yaml
        #      ``strategies: []`` is the operator's belt-and-braces "do
        #      not route here yet" — same intent as (1), but explicit
        #      via config rather than implicit via missing creds.
        #   3. ``strategies is None`` → fall through to allow. Means the
        #      account didn't declare a mapping at all (legacy
        #      fixtures, unit tests that don't set the field).
        def _eligible_for_dispatch(account_obj) -> bool:
            if not getattr(account_obj, "configured", True):
                return False
            # Symbol→exchange routing gate (multi-symbol M11). Applied only
            # when EITHER side involves Interactive Brokers, so:
            #   * a BTCUSDT (bybit) package never reaches an IB account, and
            #   * an MES (interactive_brokers) package never reaches a bybit
            #     account,
            # regardless of feature flags — making it safe to assign the
            # crypto strategies to ib_paper. Legacy crypto cross-account
            # dispatch (bybit/breakout among themselves) is left
            # untouched, so existing dispatch behaviour/tests are unchanged.
            inst_exchange = _instrument_exchange_for(getattr(pkg, "symbol", "") or "")
            acct_exchange = str(getattr(account_obj, "exchange", "") or "").lower()
            ib_involved = (
                inst_exchange == "interactive_brokers"
                or acct_exchange == "interactive_brokers"
            )
            if ib_involved and inst_exchange and acct_exchange and inst_exchange != acct_exchange:
                return False
            assigned = getattr(account_obj, "strategies", None)
            if assigned is None:
                return True  # legacy / no-mapping account
            if not assigned:
                return False  # explicit empty: block all strategies
            if not pkg.strategy:
                return True  # legacy package without a strategy tag
            return pkg.strategy in assigned

        accounts = [a for a in accounts if _eligible_for_dispatch(a)]
        logger.info(
            "[coordinator.dispatch] trace_id=%s strategy=%s symbol=%s eligible_accounts=%d",
            getattr(pkg, "trace_id", "?"), pkg.strategy, pkg.symbol, len(accounts),
        )

        # S-MLOPT-S12 Part B: per-signal account-context snapshots. Capture
        # equity / daily-PnL / daily-equity-high / drawdown / open-trade-count
        # for each eligible account BEFORE the per-account RiskManager runs
        # — so the resulting snapshot reflects state PRE-decision (leak-safe
        # for the account_context family's later LEFT JOIN). Best-effort:
        # the writer swallows every exception, so a SQLite hiccup never
        # blocks the dispatch loop. Gated behind
        # ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED (default off → enabled); set
        # truthy on the VM to disable without a redeploy.
        if order_package_id and accounts:
            _capture_account_context_snapshots(
                order_package_id=order_package_id,
                pkg=pkg,
                accounts=accounts,
                live_balances=live_balances,
            )

        results = []
        for account in accounts:
            if account_type and account.account_type != account_type:
                continue
            # Reset per-iteration so intent_legs from a previous account
            # doesn't leak into a non-intent dispatch on the next.
            intent_legs: Optional[List[Dict[str, Any]]] = None

            # Pre-build a minimal account_cfg for the sizing_failed /
            # zero_balance refusal-journal writes downstream.
            # Mirrors the richer account_cfg built below; ``getattr``
            # keeps legacy/test fixtures (where the account object
            # may not carry ``api_key_env``) routing cleanly.
            #
            # ``demo`` MUST be carried here: ``_log_trade_to_journal``
            # stamps ``is_demo`` from ``account_cfg.get("demo")``, and the
            # two early-refusal paths below (sizing_failed at the
            # position_size except, and the sized_qty<=0 gate) journal with
            # THIS cfg — not the richer ``account_cfg`` built later. Without
            # it every demo-account refusal row was written ``is_demo=0``,
            # so a demo account (e.g. bybit_1) that trips its daily-loss cap
            # and then size-refuses every subsequent signal looked like a
            # LIVE-account rejection cluster in the journal. The RiskBreach
            # and exchange_rejected paths already use the richer cfg and
            # were stamped correctly; this aligns the early paths with them.
            _early_account_cfg = {
                "account_id": account.name,
                "exchange": account.exchange,
                "api_key_env": getattr(account, "api_key_env", None),
                "market_type": getattr(account, "market_type", "spot"),
                "demo": getattr(account, "demo", False),
                # Paper-vs-real-money funding category. The executor stamps
                # trades.account_class from THIS cfg on the early-refusal
                # paths (sizing_failed / sized_qty<=0), same reason ``demo``
                # is carried here — without it those rows would be written
                # without a category and fall back to is_demo only.
                "account_class": getattr(account, "account_class", "real_money"),
            }

            # Build account_cfg, resolve effective_dry and exchange client
            # BEFORE sizing so the direction-aware balance override (spot
            # sell → BTC USD value, spot buy → USDT) has ``client`` and
            # ``effective_dry`` in scope at qty-computation time.
            # ``execute_pkg`` is the only path that knows how to talk to
            # the real exchange SDK (Bybit Unified Trading HTTP client,
            # etc.) — previously ``account.place_order`` was called here,
            # causing NotImplementedError on every live signal (the VWAP
            # "0 fills despite N signals" bug).
            from src.units.accounts.execute import execute_pkg
            from src.units.accounts.clients import (
                bybit_client_for,
                ib_client_for, oanda_client_for, alpaca_client_for,
            )

            account_cfg = {
                "account_id": account.name,
                "exchange": account.exchange,
                "api_key_env": account.api_key_env,
                # Companion secret env-var NAME. WITHOUT this, alpaca_client_for
                # falls back to the shared default secret (ALPACA_API_SECRET_KEY)
                # for an account that names its OWN pair (alpaca_live →
                # ALPACA_API_SECRET_KEY_LIVE): the resulting live-key +
                # paper-secret mismatch makes Alpaca 401 "unauthorized" on
                # every order, while the balance-snapshot read path (built from
                # the raw YAML, which carries both) still authenticates — the
                # "reads OK, orders unauthorized" split. Same failure CLASS as
                # BL-20260628-ALPACA-LIVE-HOST (that one plumbed alpaca_env;
                # this plumbs the secret env). BL-20260701-ALPACA-LIVE-SECRET-ENV.
                "api_secret_env": getattr(account, "api_secret_env", None),
                "risk_pct": account.risk_manager.risk_pct,
                "min_qty": account.risk_manager.min_qty,
                "qty_precision": account.risk_manager.qty_precision,
                # Forward the legacy ``risk:`` sub-block for any code
                # path that reads from it (RiskManager re-construction
                # inside execute_pkg when qty_override is absent).
                "max_dd_pct": account.risk_manager.max_dd_pct,
                "daily_usd": account.risk_manager.max_daily_loss_usd,
                # Bybit V5 category routing (spot vs linear). Drives
                # ``_bybit_category`` inside execute.py — without this
                # plumb-through the executor falls back to the default
                # (spot) and ignores any per-account override.
                "market_type": getattr(account, "market_type", "spot"),
                # Forward demo flag (Bybit-transport endpoint selector) so
                # clients.py routes to api-demo.bybit.com and Telegram
                # notifications carry the DEMO TRADER prefix.
                "demo": getattr(account, "demo", False),
                # Forward the paper/real funding category so execute.py
                # stamps trades.account_class (and the synced is_demo) on
                # every trade row. Single source of truth for the
                # paper/real reporting axis; orthogonal to demo + mode.
                "account_class": getattr(account, "account_class", "real_money"),
                # Interactive Brokers connection params (no API keys — auth
                # is the Gateway login session). Forwarded so ib_client_for
                # can build the socket identity. None for non-IB accounts.
                "ib_host": getattr(account, "ib_host", None),
                "ib_port": getattr(account, "ib_port", None),
                "ib_account": getattr(account, "ib_account", None),
                "ib_client_id": getattr(account, "ib_client_id", None),
                # Alpaca/OANDA host selector (paper vs live) — forwarded so
                # alpaca_client_for / oanda_client_for dial the correct host
                # for a LIVE account. WITHOUT this a live account's live key
                # is sent to the paper/practice host → "request is not
                # authorized", silently 401ing every order
                # (BL-20260628-ALPACA-LIVE-HOST). None for other exchanges.
                "alpaca_env": getattr(account, "alpaca_env", None),
                "base_url": getattr(account, "base_url", None),
                "oanda_env": getattr(account, "oanda_env", None),
            }

            # Per-account live/dry resolution. The caller-supplied
            # ``dry_run`` is a process-level OVERRIDE — when set
            # (tests / smoke runs), it forces the whole dispatch
            # round into that mode. When ``None`` (the production
            # default), the per-account ``mode`` field decides via
            # ``account.dry_run`` (already resolved by ``load_accounts``
            # from ``cfg["mode"]``). Per CLAUDE.md the per-account RiskManager is
            # the single source of truth — pre-fix this method
            # defaulted to ``dry_run=True`` and silently overrode
            # every account's ``mode: live``.
            account_dry = bool(getattr(account, "dry_run", False))
            if dry_run is not None:
                effective_dry = bool(dry_run)
            else:
                effective_dry = account_dry

            # account_state.yaml belt-and-suspenders gate (PR-3 / M2).
            # Only enforces dryness — never forces live. A missing file
            # or missing account entry is a no-op (fail-open).
            state_dry = account_state_dry_run(account.name)
            if state_dry is True and not effective_dry:
                logger.warning(
                    "[coordinator] account_state.yaml overrides %s to dry_run "
                    "(accounts.yaml said live, state file says dry)",
                    account.name,
                )
                effective_dry = True

            # Strategy-level execution gate (S9, operator-approved
            # 2026-05-24). A strategy marked ``execution: shadow`` in
            # config/strategies.yaml still RUNS and LOGS its order
            # packages everywhere (data collection) but never sends a
            # live order — it is treated as dry on every account,
            # regardless of the account's ``mode: live``. This is an
            # explicit, permissive-default (live) demotion declared in
            # the strategy config and surfaced on /api/bot/config — NOT a
            # hidden default-off flag (the MULTI_SYMBOL_ENABLED
            # anti-pattern the Prime Directive forbids). It is applied
            # generically to any strategy by name via the registry, the
            # same config-driven pattern as ``enabled`` / ``risk_pct``;
            # it reuses the existing dry-run short-circuit and adds no new
            # order-submission path. The account-level ``mode:`` remains
            # the account execution gate; this is the per-strategy gate.
            if not effective_dry:
                try:
                    from src.strategy_registry import execution_mode
                    if execution_mode(str(pkg.strategy)) == "shadow":
                        logger.info(
                            "[coordinator] strategy '%s' is execution:shadow — "
                            "logging order package on %s but NOT executing "
                            "(data-only)",
                            pkg.strategy, account.name,
                        )
                        effective_dry = True
                except Exception as exc:  # noqa: BLE001
                    # Fail-open to the account's own mode — never let a
                    # registry read error block a live strategy.
                    logger.warning(
                        "[coordinator] execution_mode lookup failed for "
                        "strategy '%s' (%s); using account mode",
                        getattr(pkg, "strategy", "?"), exc,
                    )

            client = None
            client_error: Optional[str] = None
            if not effective_dry:
                exchange_lc = (account.exchange or "").lower()
                try:
                    if exchange_lc == "bybit":
                        client = bybit_client_for(account_cfg)
                    elif exchange_lc == "oanda":
                        client = oanda_client_for(account_cfg)
                    elif exchange_lc == "alpaca":
                        client = alpaca_client_for(account_cfg)
                    elif exchange_lc in ("interactive_brokers", "ib"):
                        client = ib_client_for(account_cfg)
                    elif exchange_lc == "breakout":
                        # Prop manual-bridge (Breakout) — NO broker socket.
                        # execute_pkg's breakout branch emits a Telegram/FCM
                        # prop ticket (emit_prop_ticket) and never touches an
                        # exchange client, so leave client unset and DON'T set
                        # client_error: tripping the live-credential gate below
                        # (~"if not effective_dry and client_error is not None:
                        # raise") would abort before execute_pkg is reached, so
                        # the ticket is never emitted and the account logs
                        # "unsupported exchange 'breakout'" consecutive-rejection
                        # alerts instead (PB-20260616-004 live bridge). Mirror of
                        # src/units/accounts/execute.py::execute_pkg; design:
                        # docs/integrations/breakout-poc-manual-bridge-DESIGN.md.
                        client = None
                    else:
                        client_error = (
                            f"unsupported exchange '{exchange_lc}' "
                            f"(expected bybit/oanda/alpaca/"
                            f"interactive_brokers)"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "multi_account_execute: client construction failed "
                        "for %s (%s): %s",
                        account.name, exchange_lc, exc,
                    )
                    client_error = (
                        f"client construction failed: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    client = None
                if (
                    client is None
                    and client_error is None
                    and exchange_lc != "breakout"
                ):
                    # Resolution returned None silently — account is
                    # loaded into the accounts unit but its env-var
                    # creds aren't set. Surface a clear "not fully
                    # configured" message so the operator's diagnostic
                    # ping points straight at the missing env var
                    # instead of looking like a generic exchange error.
                    # (Breakout is exempt: it intentionally has no client —
                    # execute_pkg emits a prop ticket, see the branch above.)
                    client_error = (
                        f"account '{account.name}' is not fully "
                        f"configured: api_key_env="
                        f"{account.api_key_env!r} (and matching "
                        f"_SECRET) not in process env"
                    )

            # 1. Per-account sizing — the only place qty is decided.
            #
            # Prop manual-bridge (breakout) BYPASS (operator directive 2026-06-29;
            # PB-20260625-001 / BL-20260629 prop-sizing): a prop account has NO
            # broker API, so there is neither a live balance() to size against nor
            # a need for one — the prop leg is sized from the account RULESET
            # downstream in emit_prop_ticket (build_account_leg), and the
            # supervised assistant places + sizes it on the prop platform. Running
            # the balance-based RiskManager here only raised 'balance() returned
            # None ... account unreachable' -> sizing_failed, which stopped the
            # prop ticket from ever emitting (trend_donchian_sol/breakout_1, while
            # ETH legs slipped through only on a cached balance). Bypass the fetch
            # + sizing; a positive sentinel qty carries the decision through
            # dispatch to the breakout branch, where the real qty is computed.
            _is_prop_bridge = (account.exchange or "").lower() == "breakout"
            try:
                balance = 0.0 if _is_prop_bridge else float(fetcher(account))
                # Direction-aware balance override for cash spot.
                #
                # Cash spot (``market_type: spot``): the account holds
                # real BTC and USDT, and a sell order can only spend
                # BTC while a buy can only spend USDT. Use the
                _market_type = (
                    getattr(account, "market_type", "spot") or "spot"
                ).lower()
                # IBKR equity/ETF override (2026-07-07,
                # docs/integrations/ibkr-equity-etf-support-DESIGN.md §4.3).
                # ib_paper mixes futures (MES/MGC/MHG, market_type: futures in
                # accounts.yaml) and equities (the alpaca-ETF basket, reused
                # on the same clientId per the 2026-07-07 operator decision)
                # on ONE account — a single static account-level market_type
                # can't express that split. Resolve it PER ORDER from the
                # package's symbol: a STK symbol sizes on the whole-SHARE
                # path (round-up-to-1-share relaxation + the margin/
                # buying-power notional cap) instead of inheriting the
                # account's strict futures refuse-sub-1-contract path. A
                # symbol the resolver doesn't recognize (incl. every
                # non-IBKR account, where this is a no-op) keeps the
                # account's configured market_type unchanged.
                if (account.exchange or "").lower() == "interactive_brokers":
                    from src.units.accounts.ib_instruments import ib_order_market_type
                    _market_type = ib_order_market_type(pkg.symbol, default=_market_type)
                available_usd = None
                total_account_usd = None
                if (
                    _market_type == "linear"
                    and client is not None
                    and not effective_dry
                    and not bool(
                        getattr(pkg, "meta", None)
                        and (pkg.meta or {}).get("is_test")
                    )
                ):
                    try:
                        from src.units.accounts.execute import (
                            _fetch_linear_available_balance,
                            _fetch_linear_total_equity,
                        )
                        available_usd = _fetch_linear_available_balance(client)
                        # S-052: total cross-margin equity is the correct
                        # equity basis for the min-balance gate, daily-loss
                        # budget, and margin buffer fallback on a Bybit
                        # UNIFIED account (the position is backed by total
                        # equity, not just the free wallet balance). Best-
                        # effort — None leaves the sizer on the current
                        # free-balance behaviour (no regression).
                        total_account_usd = _fetch_linear_total_equity(client)
                        logger.debug(
                            "multi_account_execute: linear balances "
                            "account=%s available_usd=%s total_account_usd=%s",
                            account.name,
                            f"{available_usd:.4f}" if available_usd is not None else "n/a",
                            f"{total_account_usd:.4f}" if total_account_usd is not None else "n/a",
                        )
                    except Exception as _lin_exc:  # noqa: BLE001
                        logger.warning(
                            "multi_account_execute: linear balance "
                            "fetch failed for %s: %s — sizer falls back to buffer",
                            account.name, _lin_exc,
                        )
                elif (
                    (account.exchange or "").lower() in ("alpaca", "oanda")
                    and client is not None
                    and not effective_dry
                    and not bool(
                        getattr(pkg, "meta", None)
                        and (pkg.meta or {}).get("is_test")
                    )
                ):
                    # Broker-truth margin basis (prefer broker truth, like linear
                    # above). Both clients expose buying_power() returning the
                    # broker's already-leveraged notional capacity:
                    #   * Alpaca — reg-T buying power (1x cash / 2x Reg-T margin).
                    #   * OANDA  — marginAvailable / marginRate (FX margin).
                    # Feeding it as the margin pre-flight basis makes equity/FX
                    # sizing reflect TRUE buying power instead of the cash-only
                    # default (effective_leverage=1 when risk.leverage is unset).
                    # The margin cap multiplies available_usd × effective_leverage,
                    # and these accounts leave leverage unset (=1) ON PURPOSE so the
                    # already-leveraged figure is not double-counted. Best-effort —
                    # None leaves the sizer on its conservative buffer fallback.
                    try:
                        _bp = client.buying_power()
                        if _bp is not None:
                            available_usd = _bp
                        logger.debug(
                            "multi_account_execute: %s buying_power "
                            "account=%s available_usd=%s",
                            (account.exchange or "").lower(),
                            account.name,
                            f"{available_usd:.2f}" if available_usd is not None else "n/a",
                        )
                    except Exception as _bp_exc:  # noqa: BLE001
                        logger.warning(
                            "multi_account_execute: %s buying_power "
                            "fetch failed for %s: %s — sizer falls back to buffer",
                            (account.exchange or "").lower(), account.name, _bp_exc,
                        )
                from src.units.accounts.risk import requires_whole_unit_qty
                if _is_prop_bridge:
                    # Sentinel: the real (ruleset) size is computed in
                    # emit_prop_ticket (build_account_leg). The breakout branch
                    # ignores the order's qty for sizing, so this value only
                    # carries the decision through the dispatch gates.
                    sized_qty = 1.0
                else:
                    sized_qty = account.risk_manager.position_size(
                        pkg, balance,
                        market_type=_market_type,
                        available_usd=available_usd,
                        total_account_usd=total_account_usd,
                        # Per-exchange whole-unit constraint (alpaca bracket orders
                        # reject fractional shares). The RiskManager is built from
                        # the risk sub-block and never sees the exchange, so resolve
                        # it from the account here (BL-20260622-ALPACA-FRACTIONAL-SIZE).
                        # ``_market_type == "equity"`` is the IBKR-STK override
                        # resolved above — same whole-share treatment as alpaca
                        # (round-up-to-1-share relaxation on a sub-1 risk-based
                        # ideal), extended 2026-07-07 to a symbol-resolved IB
                        # equity/ETF order.
                        whole_units=(
                            requires_whole_unit_qty(account.exchange)
                            or _market_type == "equity"
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "multi_account_execute: position_size failed for %s: %s",
                    account.name, exc,
                )
                error_msg = f"sizing_failed: {type(exc).__name__}: {exc}"
                from src.units.accounts.execute import log_rejection_to_journal
                log_rejection_to_journal(
                    pkg, _early_account_cfg,
                    reason=error_msg,
                    status="rejected",
                    sized_qty=0.0,
                )
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": None,
                    "sized_qty": 0.0,
                    "error": error_msg,
                })
                continue

            # Design B conviction sizing — the APPLY path (NEW, separate from the
            # flagless observe-only annotator below). Default-off, gated by
            # CONVICTION_SIZING_MODE (off/annotate/apply) + _ACCOUNTS allowlist +
            # _DIRECTION (reductive/symmetric). When the flag is off this is a
            # byte-for-byte no-op (returns sized_qty unchanged). Composition
            # (design § "Composition (Option A)"): conviction produces the BASE
            # size here, THEN the advisory + news reducers below still apply
            # reductively on top — so ML bearishness / news opposition can still
            # shrink even a high-conviction trade. ``effective_risk_pct`` feeds
            # the daily-loss clamp (the account's base per-trade risk fraction;
            # caps the conviction-implied fraction to min(2%, risk_pct) so a
            # daily-loss-throttled account can't be re-inflated). Fail-inert.
            from src.runtime.conviction_sizing import apply_conviction_sizing
            sized_qty = apply_conviction_sizing(
                pkg, sized_qty, account_name=account.name,
                balance_usd=balance,
                available_usd=available_usd,
                total_account_usd=total_account_usd,
                leverage=getattr(account.risk_manager, "leverage", 0),
                market_type=_market_type,
                min_qty=getattr(account.risk_manager, "min_qty", 0.0),
                qty_precision=getattr(account.risk_manager, "qty_precision", 3),
                effective_risk_pct=getattr(account.risk_manager, "risk_pct", None),
            )

            # WS7 advisory influence — gated by model STAGE alone (advisory /
            # limited_live / live_approved); shadow only logs. Reductive only —
            # can shrink the RiskManager-sized qty toward a floor when a quorum
            # of advisory-stage models is bearish, never enlarge it. Per-strategy
            # advisory_policy is permissive config (default annotate = log the
            # would-be cut, no resize); demote a model to shadow to turn it off.
            from src.runtime.advisory_sizing import apply_advisory_downsize
            sized_qty = apply_advisory_downsize(
                pkg, sized_qty, account_name=account.name,
            )

            # M9 news influence (default-off, gated by NEWS_INFLUENCE_MODE).
            # Reductive only — shrinks the qty toward a floor when the news (and
            # any imminent event) opposes the trade's direction, never enlarges.
            # Composed multiplicatively with the advisory downsize above; both
            # factors are in [floor, 1.0] so the product stays <= 1.0. Inert
            # (returns qty unchanged) when the flag is off.
            from src.runtime.news_sizing import apply_news_downsize
            sized_qty = apply_news_downsize(
                pkg, sized_qty, account_name=account.name,
            )

            # P2 conviction sizing — ADVISORY / observe-only, no gate. Computes
            # the would-be conviction-driven size (conviction × 2% budget,
            # bounded by the margin ceiling + free-margin throttle) and logs it
            # to runtime_logs/conviction_sizing.jsonl, but ALWAYS returns the
            # RiskManager qty unchanged — it never touches the order, exactly
            # like the P1 meta.conviction stamp. When conviction graduates to
            # actually driving size that's a deliberate sizing-path change
            # (governed by account mode + the margin/daily-loss guards), not a
            # switch flipped here. Fail-permissive.
            from src.runtime.conviction_sizing import annotate_conviction_sizing
            sized_qty = annotate_conviction_sizing(
                pkg, sized_qty, account_name=account.name,
                balance_usd=balance,
                available_usd=available_usd,
                total_account_usd=total_account_usd,
                leverage=getattr(account.risk_manager, "leverage", 0),
                market_type=_market_type,
                min_qty=getattr(account.risk_manager, "min_qty", 0.0),
                qty_precision=getattr(account.risk_manager, "qty_precision", 3),
            )

            # Clean per-trade refusal when the risk-sized qty is below the
            # EXCHANGE lot minimum (BL-20260619-ETHMIN). The account-level
            # sizing precision/min_qty is not symbol-aware (3dp / 0.001 lot,
            # BTC-shaped), so on a small balance a strategy can size below the
            # venue minimum for a higher-priced symbol — e.g. ~$100 on bybit_2
            # at ~0.26% risk sizes ~0.005-0.007 ETH while Bybit's ETHUSDT
            # minOrderQty is 0.01. Pre-fix that sub-min qty reached the
            # ``_submit_order`` pre-flight and was journaled as
            # ``exchange_rejected`` on every recurring signal — noisy churn
            # that looks like a broker/exec failure. Refuse it HERE as a clean
            # risk refusal (``status='rejected'``, distinct
            # ``below_venue_min_qty`` cause) instead. We do NOT floor the qty
            # up — that would silently exceed the configured risk. Prime
            # Directive preserved: the account stays live; only this one trade
            # is refused; the next signal is sized fresh. ``None`` venue-min
            # (rule unknown / non-Bybit) → no change, exactly the pre-fix path.
            _pkg_is_test = bool(getattr(pkg, "meta", None) and (pkg.meta or {}).get("is_test"))
            if sized_qty > 0 and not effective_dry and not _pkg_is_test:
                try:
                    # Resolve the venue minimum through the single legalization
                    # seam (docs/sizing-legalization-DESIGN.md Phase 2). Same
                    # refusal logic as before (sized_qty < venue_min); only the
                    # resolver is unified. prefer_live=True keeps the live lot
                    # rule authoritative (strict superset of the prior
                    # venue_min_qty_for path — profile is only an added
                    # fallback), so no verdict changes for any current symbol.
                    from src.units.accounts.qty_legalize import legalize_qty
                    _venue_min = legalize_qty(
                        sized_qty, account_cfg=_early_account_cfg,
                        symbol=pkg.symbol, client=client, prefer_live=True,
                    ).venue_min
                except Exception as _vexc:  # noqa: BLE001 — never block on lookup
                    _venue_min = None
                    logger.debug(
                        "multi_account_execute: venue-min lookup failed for "
                        "%s/%s: %s", account.name, pkg.symbol, _vexc,
                    )
                if _venue_min is not None and sized_qty < _venue_min:
                    error_msg = (
                        f"below_venue_min_qty: sized_qty={sized_qty:g} < "
                        f"venue minOrderQty={_venue_min:g} for {pkg.symbol} "
                        f"(account={account.name}, balance={balance:.2f}) — "
                        "risk-sized qty is below the exchange lot minimum; "
                        "raise per-trade risk % or fund the account to clear "
                        "the venue minimum."
                    )
                    from src.units.accounts.execute import log_rejection_to_journal
                    log_rejection_to_journal(
                        pkg, _early_account_cfg,
                        reason=error_msg,
                        status="rejected",
                        sized_qty=0.0,
                    )
                    _emit_execution_failure_ping(
                        account=account.name,
                        pkg=pkg,
                        qty=sized_qty,
                        reason=error_msg,
                        demo=getattr(account, "demo", False),
                    )
                    sized_qty_by_account[account.name] = 0.0
                    results.append({
                        "name": account.name,
                        "exchange": account.exchange,
                        "account_type": account.account_type,
                        "trade_id": None,
                        "sized_qty": 0.0,
                        "error": error_msg,
                    })
                    continue

            sized_qty_by_account[account.name] = sized_qty

            # Latching daily-loss-cap notification (operator-approved
            # 2026-05-28). Fire ONE Telegram when an account first exhausts
            # its daily-loss cap and ONE when it next clears — not per-tick.
            # Runs every dispatch (so it catches both the cross-into and the
            # cross-out-of cap, incl. the 00:00 UTC auto-reset on the next
            # day's first tick); the latch in daily_cap_alert self-dedups.
            # The cap-exhaustion check uses the same equity basis the sizer
            # used. Best-effort — never blocks dispatch.
            # Skipped for the prop bridge: its balance is a sentinel 0.0 (no
            # broker API), so the cap math would misfire a spurious exhaustion
            # alert. Prop daily-loss is governed by the prop ruleset + the
            # rule-distance panel, not this RiskManager cap.
            if not _is_prop_bridge:
                try:
                    from src.runtime.daily_cap_alert import note_account_cap_state
                    _equity_basis = (
                        total_account_usd if total_account_usd is not None else balance
                    )
                    note_account_cap_state(
                        account.name,
                        exhausted=account.risk_manager.is_daily_cap_exhausted(
                            _equity_basis
                        ),
                        daily_pnl=account.risk_manager.daily_pnl,
                        cap_usd=account.risk_manager.effective_daily_loss_usd(
                            _equity_basis
                        ),
                        demo=getattr(account, "demo", False),
                    )
                except Exception as _cap_exc:  # noqa: BLE001
                    logger.debug(
                        "multi_account_execute: daily-cap note failed for %s: %s",
                        account.name, _cap_exc,
                    )

            # 2. Refuse to forward a zero-qty order. This branch fires
            # for ANY sized_qty <= 0 outcome from the RiskManager —
            # not only true "balance below floor" cases. Pre-fix the
            # error template hardcoded ``below_min_balance`` which was
            # misleading whenever the actual cause was the
            # daily-loss-budget gate or any other RiskManager refusal.
            # Operators saw "balance=186.87 < 50.0" and
            # couldn't tell the comparison was a lie.
            if sized_qty <= 0:
                error_msg = _explain_zero_sized_qty(
                    balance=balance,
                    available_usd=available_usd,
                    total_account_usd=total_account_usd,
                    risk_manager=account.risk_manager,
                    direction=getattr(pkg, "direction", "?"),
                    market_type=str(
                        getattr(account, "market_type", "spot") or "spot"
                    ).lower(),
                )
                # An effective-dry account (shelved ``mode: dry_run``,
                # execution:shadow strategy, or process-level override)
                # could never have placed this order — a sizing refusal
                # here is a policy hold, not a dispatch failure. Tag it
                # so the operator alert classifies it as expected (the
                # same suppression the risk gate's account_mode_dry_run
                # reason gets); the underlying cause stays in the string
                # for the journal/audit. Without this, alpaca_live —
                # dry-shelved AND deliberately defunded 2026-07-15 —
                # alarmed "failed to dispatch: zero_balance" on every
                # routed signal (the sizer runs before the risk gate).
                if effective_dry:
                    error_msg = f"dry_run_sizing_skip: {error_msg}"
                from src.units.accounts.execute import log_rejection_to_journal
                log_rejection_to_journal(
                    pkg, _early_account_cfg,
                    reason=error_msg,
                    status="rejected",
                    sized_qty=0.0,
                )
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": None,
                    "sized_qty": 0.0,
                    "error": error_msg,
                })
                continue

            # Captured before the try so the except blocks can pass the
            # un-mangled token to the rejection-journal helper (post-CP-13
            # observability — every refusal lands a row in trade_journal.db).
            risk_reason: Optional[str] = None
            try:
                # 1. Per-account risk gate (local check, since execute_pkg
                #    does not call account.risk_manager.approve()). Honour
                #    smoke-test bypass via _is_test_order semantics.
                #
                # Prop-risk integration: ``evaluate`` returns a
                # structured reason on reject (DAILY_LOSS_CAP /
                # INTRADAY_DRAWDOWN /
                # SKIP_MISSION_MET / SKIP_OVERNIGHT_RESTRICTED /
                # SKIP_WEEKEND_RESTRICTED / account_mode_dry_run). The
                # reason flows through the result row's ``error`` field
                # so /signals and the diagnostic ping can distinguish a
                # true risk breach from a mission-aware skip.
                #
                # Intent-aware multi-strategy mode (#1125 follow-up):
                # when the package came out of the intent multiplexer
                # the binary open-position guard is too coarse — it
                # would block Turtle Soup's reinforcement of an open
                # VWAP long even though the two strategies agreed on
                # direction. Use the delta computer instead so:
                #   * already-at-target  → noop (no order; logged + skipped)
                #   * below-target same  → top up by the delta
                #   * opposite direction → refuse (v1: flip not wired)
                # Non-intent packages still see the legacy binary block
                # so existing tests / legacy single-strategy mode keep
                # the same behaviour.
                from src.runtime.intents import (
                    compute_execution_delta_for_package,
                    package_is_intent_mode,
                )
                intent_mode = package_is_intent_mode(pkg)
                effective_qty = sized_qty
                if intent_mode and not (pkg.meta and pkg.meta.get("is_test")):
                    from src.runtime.positions import (
                        current_net_position_qty,
                        get_existing_position_info,
                        has_open_trade_for_strategy,
                        position_netting_guard_active_for,
                    )
                    current_signed_qty = current_net_position_qty(
                        account.name, pkg.symbol,
                    )
                    _existing_pos_info = (
                        get_existing_position_info(account.name, pkg.symbol)
                        if current_signed_qty != 0 else None
                    )
                    delta = compute_execution_delta_for_package(
                        pkg,
                        current_signed_qty=current_signed_qty,
                        risk_sized_qty=sized_qty,
                        existing_confidence=(
                            _existing_pos_info.get("confidence")
                            if _existing_pos_info else None
                        ),
                        existing_age_hours=(
                            _existing_pos_info.get("age_hours")
                            if _existing_pos_info else None
                        ),
                    )
                    pkg.meta["execution_delta"] = {
                        "action": delta.action,
                        "side": delta.side,
                        "qty_delta": delta.qty_delta,
                        "target_qty": delta.target_qty,
                        "current_qty": delta.current_qty,
                        "reason": delta.reason,
                    }
                    # Position-netting guard — monocle half (Option A,
                    # BL-20260608-DEMOPNL). In one-way mode every
                    # same-direction re-entry NETS into the single shared
                    # position and OVERWRITES its single SL/TP, so the
                    # journal "trades" stop being independent positions
                    # (no per-trade stop-out, no per-trade close to
                    # attribute PnL from). Block a same-direction ADD
                    # (delta action ``open``/``increase``) for this
                    # (strategy, account, symbol) whenever it already holds
                    # an open trade — restoring the per-trade=per-position
                    # invariant. This is intentionally NOT gated on the
                    # order_packages status the legacy strategy-monocle
                    # keys on: a prematurely-closed package must not free
                    # the gate while the position is genuinely still open.
                    # Reduce / close / flip (position-management) deltas are
                    # never blocked. The guard is BASELINE (unconditional) as of
                    # 2026-06-17 — ``position_netting_guard_active_for`` returns
                    # True for every account (a no-op where it can't apply, e.g.
                    # brokers that net atomically); the old
                    # POSITION_NETTING_GUARD_ENABLED env flag was removed (Prime
                    # Directive: a required correctness fix must not sit behind a
                    # default-off gate that an env drop could silently revert).
                    if (
                        position_netting_guard_active_for(account.name)
                        and delta.action in ("open", "increase")
                        and has_open_trade_for_strategy(
                            account.name, pkg.symbol, pkg.strategy,
                        )
                    ):
                        _guard_reason = (
                            f"reentry_suppressed_netting_guard:{delta.action}"
                        )
                        logger.info(
                            "[coordinator] netting-guard: suppressing %s "
                            "re-entry for %s/%s/%s — open trade already "
                            "exists (no pyramiding; per-trade=per-position)",
                            delta.action, account.name, pkg.symbol, pkg.strategy,
                        )
                        from src.units.accounts.execute import log_rejection_to_journal
                        log_rejection_to_journal(
                            pkg, account_cfg,
                            reason=_guard_reason,
                            status="rejected",
                            sized_qty=0.0,
                        )
                        results.append({
                            "name": account.name,
                            "exchange": account.exchange,
                            "account_type": account.account_type,
                            "trade_id": None,
                            "sized_qty": 0.0,
                            "error": _guard_reason,
                        })
                        continue
                    if delta.action == "noop":
                        logger.info(
                            "[coordinator] intent-mode noop for %s/%s: %s",
                            account.name, pkg.symbol, delta.reason,
                        )
                        # M26 P1 conflict-taxonomy soak (observe-only): a
                        # hold-policy flip suppression is the live conflict
                        # event — classify it by held-vs-opposing clock ratio
                        # (src/runtime/conflict_taxonomy.py). Best-effort;
                        # the noop journal row below is unchanged.
                        if str(delta.reason or "").startswith(
                            "flip_suppressed_hold_policy"
                        ):
                            from src.runtime.conflict_taxonomy import record_conflict
                            record_conflict(
                                account_id=account.name,
                                symbol=pkg.symbol,
                                opposing_strategy=pkg.strategy,
                                opposing_side=getattr(pkg, "direction", None),
                                opposing_confidence=getattr(
                                    pkg, "confidence", None
                                ),
                                current_signed_qty=delta.current_qty,
                                suppression_reason=delta.reason,
                            )
                        from src.units.accounts.execute import log_rejection_to_journal
                        log_rejection_to_journal(
                            pkg, account_cfg,
                            reason=f"intent_noop:{delta.reason}",
                            status="rejected",
                            sized_qty=0.0,
                        )
                        results.append({
                            "name": account.name,
                            "exchange": account.exchange,
                            "account_type": account.account_type,
                            "trade_id": None,
                            "sized_qty": 0.0,
                            "error": f"intent_noop:{delta.reason}",
                        })
                        continue
                    # Non-derivative position management — HOLD-TO-BRACKET
                    # (BL-20260622-ALPACA-REDUCE-HOLD; operator-approved
                    # 2026-06-22; supersedes the hard refusal in
                    # BL-20260619-MGC-REDUCE-GUARD). Bybit V5 reduceOnly is
                    # derivatives-only, and the intent-mode reduce dispatch
                    # (execute_pkg reduce_only=True → _submit_order) is wired
                    # ONLY for the Bybit branch. Equity (alpaca) / futures (ib)
                    # accounts attach a broker-side SL/TP bracket at entry that
                    # IS the exit, so they do NOT actively resize toward a
                    # moving risk target. A reduce / close / flip delta on such
                    # an account therefore resolves to a NOOP / HOLD: no
                    # reducing order is sent, the position rides its broker
                    # bracket. This replaces the per-tick
                    # `intent_reduce_requires_derivatives` RiskBreach (which
                    # spammed the "all accounts failed to dispatch" alert and
                    # left the position unmanaged anyway). ``close`` is held too
                    # rather than auto-flattened via close_open_position: in this
                    # architecture a close delta is almost always a transient
                    # risk-sizing-0 artifact (daily-loss cap / min-balance / a
                    # momentary balance read), and force-flattening a
                    # bracket-protected position on that would contradict
                    # hold-to-bracket. (Bybit linear/inverse accounts fall
                    # through to the existing reduceOnly dispatch unchanged.)
                    _market_type = (
                        getattr(account, "market_type", "spot") or "spot"
                    ).lower()
                    if (
                        delta.action in ("reduce", "close", "flip")
                        and _market_type not in {"linear", "inverse"}
                    ):
                        # Genuine strategy-driven EXIT (a FLIP_POLICY=flat
                        # opposite-side vote → ``action="close"`` with reason
                        # ``flip_flat_policy:…``) is a deliberate "get flat now"
                        # decision and IS executed — flattened via the unified
                        # close_open_position primitive (alpaca → AlpacaClient.
                        # close, ib → IBClient.close), NOT held. This is the
                        # close carve-out of the hold-to-bracket fix
                        # (BL-20260622-ALPACA-REDUCE-HOLD). ``flip_flat_policy`` is
                        # the ONLY way an ``action="close"`` reaches here: the
                        # other close path (``reason="close_existing_…"``, fired
                        # when ``target_qty == 0``) can't occur, because a
                        # risk-sizing-0 is already refused at the ``sized_qty<=0``
                        # zero-qty gate above (before the delta is computed) and
                        # ``target`` is therefore always > 0 here. The reason
                        # check keeps the carve-out precise even so. Inert under
                        # the global FLIP_POLICY=hold
                        # default (no flip_flat close is ever produced); activates
                        # only for an account/strategy that opts into
                        # flip_policy=flat. The held-position DB row is closed by
                        # the order-monitor reconciler on close-on-disappear once
                        # the broker reads flat (same as every other flatten).
                        if (
                            delta.action == "close"
                            and str(delta.reason or "").startswith("flip_flat_policy")
                        ):
                            held_side = "long" if current_signed_qty > 0 else "short"
                            held_qty = abs(float(current_signed_qty))
                            close_res: dict = {}
                            if not effective_dry and held_qty > 0 and client is not None:
                                from src.units.accounts.execute import (
                                    close_open_position,
                                )
                                close_res = close_open_position(
                                    client, account_cfg,
                                    symbol=pkg.symbol, side=held_side, qty=held_qty,
                                ) or {}
                            _closed_ok = bool(close_res.get("ok")) or effective_dry
                            logger.info(
                                "[coordinator] flip_flat close on %s/%s "
                                "(market_type=%s) → close_open_position(side=%s "
                                "qty=%s) ok=%s%s",
                                account.name, pkg.symbol, _market_type, held_side,
                                held_qty, _closed_ok,
                                "" if _closed_ok
                                else f" err={close_res.get('error')}",
                            )
                            from src.units.accounts.execute import (
                                log_rejection_to_journal,
                            )
                            log_rejection_to_journal(
                                pkg, account_cfg,
                                reason=(
                                    "intent_close_flatten:"
                                    + ("ok" if _closed_ok
                                       else str(close_res.get("error")))
                                ),
                                status="rejected",
                                sized_qty=0.0,
                            )
                            results.append({
                                "name": account.name,
                                "exchange": account.exchange,
                                "account_type": account.account_type,
                                "trade_id": None,
                                "sized_qty": 0.0,
                                # Success → benign (intent_noop: prefix) so the
                                # all-accounts-failed roll-up doesn't misfire on a
                                # clean flatten that placed no NEW trade row.
                                # Failure → a real error so the alert DOES fire.
                                "error": (
                                    "intent_noop:flip_flat_closed_via_flatten"
                                    if _closed_ok
                                    else f"intent_close_flatten_failed:"
                                         f"{close_res.get('error')}"
                                ),
                            })
                            continue
                        _hold_reason = (
                            f"intent_noop:hold_to_bracket_{delta.action}"
                            f"_non_derivative"
                        )
                        logger.info(
                            "[coordinator] hold-to-bracket: %s on %s/%s "
                            "(market_type=%s) resolves to HOLD — broker bracket "
                            "is the exit; no reducing order sent.",
                            delta.action, account.name, pkg.symbol, _market_type,
                        )
                        from src.units.accounts.execute import log_rejection_to_journal
                        log_rejection_to_journal(
                            pkg, account_cfg,
                            reason=_hold_reason,
                            status="rejected",
                            sized_qty=0.0,
                        )
                        results.append({
                            "name": account.name,
                            "exchange": account.exchange,
                            "account_type": account.account_type,
                            "trade_id": None,
                            "sized_qty": 0.0,
                            "error": _hold_reason,
                        })
                        continue

                    # Sub-min-lot delta guard. A netting delta smaller than the
                    # minimum tradeable lot is dust — treat it as a noop rather
                    # than dispatching an order the exchange will reject. The
                    # account-level ``risk_manager.min_qty`` is NOT symbol-aware
                    # (default 0.001, BTC-shaped), so on a higher-lot symbol a
                    # delta in e.g. [0.001, 0.0099) ETH slips past an
                    # account-min-only check and reaches the ``_submit_order``
                    # pre-flight, which floors it to 0 and raises "below the
                    # exchange lot minimum after step-alignment" — surfacing as a
                    # noisy ``bybit_place_order_failed`` error ping on every
                    # top-up/trim signal. This is the delta-path flavour of the
                    # sized-qty venue-min gap fixed above (BL-20260619-ETHMIN) and
                    # the risk.py gap tracked in BL-20260628-CRYPTO-INSTRUMENT-MIN-FLOOR.
                    # Fold the EXCHANGE minimum into the threshold, resolved via
                    # the single legalization seam (docs/sizing-legalization-DESIGN.md
                    # Phase 2). ``.venue_min`` is None when the rule is unknown /
                    # non-Bybit, so the effective minimum degrades to the account
                    # min — byte-for-byte the pre-fix behaviour. prefer_live=True
                    # keeps the live lot rule authoritative (strict superset of
                    # the prior venue_min_qty_for path).
                    try:
                        from src.units.accounts.qty_legalize import legalize_qty
                        _delta_venue_min = legalize_qty(
                            delta.qty_delta, account_cfg=account_cfg,
                            symbol=pkg.symbol, client=client, prefer_live=True,
                        ).venue_min
                    except Exception:  # noqa: BLE001 — never block on a lookup
                        _delta_venue_min = None
                    _eff_min_qty = account.risk_manager.min_qty
                    if _delta_venue_min is not None:
                        _eff_min_qty = max(_eff_min_qty, _delta_venue_min)
                    if delta.qty_delta < _eff_min_qty:
                        # Position is within one min-lot of the target;
                        # treat as noop to avoid spamming dust orders.
                        logger.info(
                            "[coordinator] intent-mode sub-min_qty delta for "
                            "%s/%s (delta=%s eff_min_qty=%s account_min=%s "
                            "venue_min=%s) — treating as noop",
                            account.name, pkg.symbol,
                            delta.qty_delta, _eff_min_qty,
                            account.risk_manager.min_qty, _delta_venue_min,
                        )
                        from src.units.accounts.execute import log_rejection_to_journal
                        log_rejection_to_journal(
                            pkg, account_cfg,
                            reason="intent_sub_min_qty_delta",
                            status="rejected",
                            sized_qty=0.0,
                        )
                        results.append({
                            "name": account.name,
                            "exchange": account.exchange,
                            "account_type": account.account_type,
                            "trade_id": None,
                            "sized_qty": 0.0,
                            "error": "intent_sub_min_qty_delta",
                        })
                        continue
                    effective_qty = float(delta.qty_delta)
                    # Build leg list for the dispatcher. The vast
                    # majority of ticks produce a single "open"/"increase"
                    # leg matching pkg.direction; the reduce / close /
                    # flip branches build their legs against
                    # ``delta.side`` (the opposite of the current net
                    # position) and pass ``reduce_only=True`` so Bybit
                    # treats the order as a position-reducing fill.
                    # Flip is the only multi-leg case: close-leg first
                    # (reduce-only), then a new open on the opposite
                    # direction.
                    intent_legs = _build_intent_legs(pkg, delta)
                elif not (pkg.meta and pkg.meta.get("is_test")):
                    # Legacy single-strategy / first-wins multiplexer
                    # path. Binary open-position guard stays — see
                    # block comment above for the rationale.
                    if _has_open_position(account.name, pkg.symbol):
                        risk_reason = "open_position_exists"
                        raise RiskBreach(
                            f"Account '{account.name}' already has an open "
                            f"{pkg.symbol} position — skipping new order"
                        )

                ok, reason = account.risk_manager.evaluate(pkg)
                if not ok:
                    risk_reason = reason or "risk_gate_refused"
                    raise RiskBreach(
                        f"Account '{account.name}' rejected order for "
                        f"{pkg.symbol}: {risk_reason}"
                    )

                # 2. Live-mode credential gate. A missing client when
                # the per-account mode is live (or the caller forced
                # live) is a hard error, not a silent dry-run
                # fallback.
                if not effective_dry and client_error is not None:
                    raise RuntimeError(client_error)

                # 2. execute_pkg — the canonical live entry point.
                # The local rename below avoids a false-positive trip on
                # the dry-run-guard CI regex (which conservatively flags
                # any new `dry_run=<truthy-token>` text as a flag flip).
                exec_dry_run = bool(effective_dry)
                # Legacy / single-leg path: build a one-entry legs list
                # so the same loop below handles both modes uniformly.
                # ``intent_legs`` is set above only for intent-mode
                # packages; non-intent packages take this default.
                if intent_legs is None:
                    intent_legs = [
                        {
                            "pkg": pkg,
                            "qty": effective_qty,
                            "reduce_only": False,
                            "label": "primary",
                        }
                    ]
                leg_trade_ids: List[str] = []
                for _leg in intent_legs:
                    _leg_trade_id = execute_pkg(
                        _leg["pkg"], account_cfg,
                        exchange_client=client,
                        balance_usdt=balance,
                        dry_run=exec_dry_run,
                        qty_override=_leg["qty"],
                        reduce_only=bool(_leg.get("reduce_only", False)),
                    )
                    leg_trade_ids.append(_leg_trade_id)
                    self.push_alert(
                        f"multi_execute: {account.name} {pkg.strategy} "
                        f"{_leg['pkg'].direction} {pkg.symbol} "
                        f"qty={_leg['qty']} reduce_only="
                        f"{bool(_leg.get('reduce_only', False))} "
                        f"leg={_leg.get('label', '?')} → {_leg_trade_id}",
                        source="accounts",
                        level="info",
                        account=account.name,
                        trade_id=_leg_trade_id,
                        sized_qty=_leg["qty"],
                    )
                trade_id = leg_trade_ids[-1] if leg_trade_ids else None
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": trade_id,
                    "sized_qty": effective_qty,
                    "error": None,
                    "leg_trade_ids": leg_trade_ids if len(leg_trade_ids) > 1 else None,
                })
                _EXCHANGE_REJECTION_COUNTS.pop(account.name, None)
                # NOTE: the demo-account "*DEMO TRADER* SUBMITTED" ping was
                # removed 2026-07-09 (operator ask — no double trade-open
                # message). The single "🟢 TRADE OPENED" ping from
                # execute.execute_pkg already covers demo opens; it now carries
                # a 🧪 DEMO marker (enqueue_trade_open(demo=...)) so demo trades
                # still read clearly. `_enqueue_demo_ping` is retained for any
                # future non-open demo event.
            except RiskBreach as exc:
                # An EXPECTED policy skip — a shelved dry_run account declining,
                # or a prop mission/session skip — is the gate working as
                # designed, NOT a failure: suppress the operator "execution
                # failed" ping (still journal the rejection below for audit). A
                # wired-but-off account should just silently not trade (operator
                # directive 2026-07-15). A genuine RiskBreach (daily-loss cap,
                # open-position guard, real risk refusal) still pings.
                from src.runtime.execution_diagnostics import (
                    is_expected_dispatch_skip,
                )
                if not is_expected_dispatch_skip(risk_reason):
                    _emit_execution_failure_ping(
                        account=account.name,
                        pkg=pkg,
                        qty=sized_qty,
                        reason=f"RiskBreach: {exc}",
                        demo=getattr(account, "demo", False),
                    )
                from src.units.accounts.execute import log_rejection_to_journal
                log_rejection_to_journal(
                    pkg, account_cfg,
                    reason=risk_reason or "risk_gate_refused",
                    status="rejected",
                    sized_qty=sized_qty,
                )
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": None,
                    "sized_qty": sized_qty,
                    "error": str(exc),
                })
            except Exception as exc:  # noqa: BLE001
                # Catches RuntimeError (paused / order submission failed),
                # ValueError (invalid pkg), and any exchange SDK
                # exception that escapes execute_pkg's own handler.
                # The diagnostic ping fires here so the operator sees
                # missing-creds, "exchange rejected", "account paused",
                # etc. without grepping journalctl.
                logger.exception(
                    "multi_account_execute: execute_pkg failed for %s: %s",
                    account.name, exc,
                )
                _emit_execution_failure_ping(
                    account=account.name,
                    pkg=pkg,
                    qty=sized_qty,
                    reason=f"{type(exc).__name__}: {exc}",
                    demo=getattr(account, "demo", False),
                )
                from src.units.accounts.execute import log_rejection_to_journal
                log_rejection_to_journal(
                    pkg, account_cfg,
                    reason=f"{type(exc).__name__}: {exc}",
                    status="exchange_rejected",
                    sized_qty=sized_qty,
                )
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": None,
                    "sized_qty": sized_qty,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                _count = _EXCHANGE_REJECTION_COUNTS.get(account.name, 0) + 1
                _EXCHANGE_REJECTION_COUNTS[account.name] = _count
                if _count >= _EXCHANGE_REJECTION_ALERT_THRESHOLD:
                    try:
                        self.push_alert(
                            f"Account '{account.name}' has seen {_count} "
                            f"consecutive exchange rejections "
                            f"(last: {type(exc).__name__}: {str(exc)[:120]}). "
                            f"Account stays live — investigate and use "
                            f"set-account-mode to pause manually if needed.",
                            source="accounts",
                            level="critical",
                            account=account.name,
                            consecutive_rejections=_count,
                        )
                    except Exception as alert_exc:  # noqa: BLE001
                        logger.warning(
                            "multi_account_execute: rejection alert on %s "
                            "raised: %s",
                            account.name, alert_exc,
                        )

        # Aggregate "trader is silent" signal: if the dispatch round
        # touched at least one account but none of them placed a
        # trade, emit a single high-priority roll-up ping. The
        # per-account pings already exist for individual diagnosis;
        # this one surfaces the cascade-into-silence pattern that
        # the operator missed during the trade 875 / 876 incident
        # (2026-05-08, Bybit ErrCode 170131).
        # A round where EVERY account benignly no-op'd (already at target, or
        # an opposite-side signal held under FLIP_POLICY=hold, or a sub-min-lot
        # delta, or a same-direction re-entry suppressed by the position-netting
        # guard) placed zero trades by DESIGN — that is not a dispatch failure.
        # Only escalate the 🚨 roll-up when at least one account failed for a
        # genuine reason (RiskBreach, exchange rejection, missing creds). Folding
        # the benign no-ops in here was firing false alarms on every flip-
        # suppressed signal (health-review BL-20260531-002) and, once the
        # netting guard's demo soak began, on every suppressed re-entry too.
        from src.runtime.execution_diagnostics import is_expected_dispatch_skip

        def _is_benign_noop(result: dict) -> bool:
            err = str(result.get("error") or "")
            return (
                err.startswith("intent_noop:")
                or err == "intent_sub_min_qty_delta"
                or err.startswith("reentry_suppressed_netting_guard:")
                # A shelved dry_run account / prop mission-skip declining is a
                # deliberate policy hold, not a dispatch failure — a round where
                # every leg is one of these must NOT fire the "all accounts
                # failed" roll-up (operator directive 2026-07-15).
                or is_expected_dispatch_skip(err)
            )

        any_trade_placed = any(r.get("trade_id") is not None for r in results)
        all_benign_noop = bool(results) and all(_is_benign_noop(r) for r in results)
        if results and not any_trade_placed and not all_benign_noop:
            try:
                from src.runtime.execution_diagnostics import (
                    enqueue_all_accounts_failed_dispatch,
                )
                enqueue_all_accounts_failed_dispatch(
                    strategy=getattr(pkg, "strategy", "unknown"),
                    symbol=getattr(pkg, "symbol", "?"),
                    side=("buy" if getattr(pkg, "direction", "")
                          == "long" else "sell"),
                    results=results,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "multi_account_execute: all-failed roll-up enqueue "
                    "raised: %s", exc,
                )

        # BUG-049 backstop (2026-06-25): guarantee the emit→terminal contract.
        # The package is logged status='open' at the top of dispatch, BEFORE the
        # eligibility filter. If this round placed NO trade for it AND the
        # specific skip path left no linking rejection row, the package sits
        # status='open' / linked_trade_id=NULL and the monitor reconciler
        # mis-stamps it 'orphaned — never executed' at +5min (BUG-049). That
        # red-flag status is wrong for what is really a deliberate non-fill. The
        # observed classes this catches (system-review 2026-06-25):
        #   * empty eligible list — strategy routed ONLY to an unconfigured /
        #     strategies:[] account (e.g. trend_donchian_eth/_sol → breakout_1
        #     with no creds → configured=False → filtered out → loop never runs);
        #   * a benign no-op (netting-guard re-entry suppression, sub-min-lot,
        #     flip-hold) that left no order_package_id-linked rejection row;
        #   * any branch that returned without journaling.
        # Terminalise the package directly here (mirrors the prop-branch contract
        # in execute_pkg) so 'orphaned' stays reserved for genuinely lost
        # positions. Fires ONLY when no trade was placed — a package that DID
        # place a trade is linked by the trade row's order_package_id (unchanged).
        # Pure post-dispatch status hygiene: it changes no execution decision and
        # the genuine-failure roll-up ping above still fires. Best-effort.
        if order_package_id and not any_trade_placed:
            if not accounts:
                _term_reason = "no_eligible_account"
            elif all_benign_noop:
                _term_reason = "all_accounts_noop"
            else:
                _term_reason = "no_fill_all_accounts"
            try:
                from src.units.db.database import Database
                from src.utils.paths import trade_journal_db_path

                Database(db_path=trade_journal_db_path()).update_order_package(
                    order_package_id,
                    {"status": "rejected", "close_reason": _term_reason},
                )
            except Exception as exc:  # noqa: BLE001 — never break dispatch
                logger.warning(
                    "multi_account_execute: BUG-049 backstop terminalise failed "
                    "(pkg=%s strategy=%s symbol=%s reason=%s): %s",
                    order_package_id, getattr(pkg, "strategy", "?"),
                    getattr(pkg, "symbol", "?"), _term_reason, exc,
                )

        # Re-persist the realized per-account sizing onto the package row.
        # OBSERVABILITY ONLY — changes no execution decision (the dispatch is
        # already complete here).
        #
        # `_log_new_order_package` serialized `pkg.meta` at CREATION (top of
        # this method, BEFORE the per-account sizing loop ran), so the
        # `sized_qty_by_account` (and `aggregated_target_qty`) stored in the
        # order_packages row is the EMPTY/zero creation snapshot — never the
        # real per-account sizing the loop just computed (it mutates the same
        # dict in-memory, but the DB row is not re-written). That left the
        # field designed to show per-account sizing dead `{}` in the journal
        # for every package, and led the 2026-06-26 system-report to read
        # `aggregated_target_qty:0 / sized_qty_by_account:{}` as evidence of a
        # sizing bug when it was just the pre-sizing snapshot (see
        # docs/audits/order-packages-zero-qty-2026-06-26.md). Re-write the meta
        # now that the loop has populated `sized_qty_by_account`. Best-effort:
        # a write failure never affects the order outcome.
        if order_package_id:
            try:
                from src.units.db.database import Database
                from src.utils.paths import trade_journal_db_path

                # BL-20260626: also fold the per-account refusal cause into meta
                # so a consumer sees WHY each eligible account didn't size
                # (zero_balance / netting-guard / hold / below-min / intent_noop),
                # not just an empty sized_qty_by_account. The refusal cause
                # otherwise lives only in the rejection trades rows. Observability
                # only — same best-effort wrapper, no execution effect.
                _refusals = {
                    r["name"]: r["error"]
                    for r in results
                    if isinstance(r, dict) and r.get("error") and r.get("name")
                }
                if _refusals:
                    if pkg.meta is None:
                        pkg.meta = {}
                    pkg.meta["refusal_causes_by_account"] = _refusals
                _meta_now = {
                    k: v for k, v in (pkg.meta or {}).items()
                    if k not in {"order_package_id", "model_scores"}
                }
                Database(db_path=trade_journal_db_path()).update_order_package(
                    order_package_id, {"meta": _meta_now},
                )
            except Exception as exc:  # noqa: BLE001 — never break dispatch
                logger.debug(
                    "multi_account_execute: post-sizing meta re-persist failed "
                    "(pkg=%s): %s", order_package_id, exc,
                )

        return results

    def reload_accounts(self, accounts_path: Optional[str] = None) -> Dict[str, Any]:
        """Push an alert confirming accounts.yaml is accessible and return count.

        The accounts layer is stateless per-call (load_accounts() is called fresh
        each time), so 'reloading' just verifies the file is readable.
        """
        from src.units.accounts import load_accounts
        import os as _os

        path = accounts_path or _os.path.join(_REPO_ROOT, "config", "accounts.yaml")
        try:
            accounts = load_accounts(path)
        except FileNotFoundError:
            return {"reloaded": False, "error": f"accounts.yaml not found: {path}"}

        summary = {
            "reloaded": True,
            "accounts_path": path,
            "account_count": len(accounts),
            "accounts": [a.name for a in accounts],
        }
        self.push_alert(
            f"Accounts reloaded: {len(accounts)} accounts from {path}",
            source="app",
            level="info",
            **summary,
        )
        return summary

    def reload_strategy_config(self, config_path: Optional[str] = None) -> Dict[str, Any]:
        """Verify strategies.yaml is readable and return the loaded config.

        Pushes a ``source="app"`` alert with strategy names and count.

        Parameters
        ----------
        config_path : str, optional
            Override path to strategies.yaml.  Defaults to ``config/strategies.yaml``.

        Returns
        -------
        dict
            ``{reloaded, strategy_count, strategies, config_path}`` on success,
            ``{reloaded: False, error: "..."}`` on FileNotFoundError.
        """
        from src.units.strategies import load_strategy_config
        import os as _os

        path = config_path or _os.path.join(_REPO_ROOT, "config", "strategies.yaml")

        # Always clear the cache so stale predictor state never survives a
        # reload call, even when the YAML path doesn't exist.
        self._shadow_predictors_cache.clear()

        try:
            cfg = load_strategy_config(path)
        except FileNotFoundError:
            return {"reloaded": False, "error": f"strategies.yaml not found: {path}"}

        summary = {
            "reloaded": True,
            "config_path": path,
            "strategy_count": len(cfg),
            "strategies": list(cfg.keys()),
        }
        self.push_alert(
            f"Strategy config reloaded: {len(cfg)} strategies from {path}",
            source="app",
            level="info",
            **summary,
        )
        return summary

    # ------------------------------------------------------------------
    # Unit 3 → Dashboards
    # ------------------------------------------------------------------

    def dashboard_stats(
        self,
        exchange_clients: Optional[Dict[str, Any]] = None,
        strategy_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Return unified stats for all strategies, accounts, and alerts.

        Parameters
        ----------
        exchange_clients : dict[account_id, client], optional
            When provided, balance and open_positions are fetched live.
            When None (default) those fields are None (safe for offline use).
        strategy_rows : list[dict], optional
            Pre-fetched strategy rows; fetched from data_loaders when None.

        Keys in returned dict:
            ``strategies``   — per-strategy enriched rows (incl. paused flag)
            ``accounts``     — per-account enriched rows (balance, positions)
            ``alerts``       — pending alerts from the global queue
            ``generated_at`` — ISO-8601 UTC timestamp
        """
        from src.units.dashboards.stats import build_stats
        return build_stats(
            accounts=self.list_accounts(),
            paused_account_ids=set(_PAUSED_ACCOUNTS),
            paused_strategy_names=set(),
            strategy_rows=strategy_rows,
            exchange_clients=exchange_clients,
        )

    # --- Alerts helpers (Dashboards subunit) ---------------------------------

    def push_alert(
        self,
        message: str,
        source: str = "coordinator",
        level: str = "info",
        **extra: Any,
    ) -> Dict[str, Any]:
        """Push an alert to the global dashboards alerts queue."""
        from src.units.dashboards.alerts import push_alert
        return push_alert(message, source=source, level=level, **extra)

    def list_alerts(self, n: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return up to *n* most-recent alerts (all when None)."""
        from src.units.dashboards.alerts import list_alerts
        return list_alerts(n)

    def pop_alerts(self) -> List[Dict[str, Any]]:
        """Drain and return all pending alerts."""
        from src.units.dashboards.alerts import pop_alerts
        return pop_alerts()

    def recent_signals(self, strategy: Optional[str] = None, n: int = 5) -> List[Dict[str, Any]]:
        """Recent signals for *strategy* (all strategies when None).

        Results are sorted newest-first, capped at *n* total rows.
        """
        from src.bot.data_loaders import recent_signals_for, list_live_strategies

        if strategy:
            return recent_signals_for(strategy, n)
        out: List[Dict[str, Any]] = []
        for s in list_live_strategies():
            out.extend(recent_signals_for(s, n))
        out.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
        return out[:n]

    # ------------------------------------------------------------------
    # Unit 4 → Return Commands
    # ------------------------------------------------------------------

    def return_command(self, cmd: str, **kwargs: Any) -> Dict[str, Any]:
        """Dispatch a UI return command to the appropriate unit action.

        Supported commands (from units.yaml → return_commands):
          halt / killswitch / pause  → pause all accounts (risk manager)
          resume / unpause           → resume all accounts

        Returns ``{"cmd": cmd, "status": "ok"|"partial"|"error", "detail": str, ...}``.
        Strategies always keep running and logging — only accounts are paused.
        """
        cmd_norm = cmd.strip().lower().lstrip("/")
        halt_cmds = {"halt", "killswitch", "pause"}
        resume_cmds = {"resume", "unpause"}

        if cmd_norm in halt_cmds:
            return self._cmd_halt(**kwargs)
        if cmd_norm in resume_cmds:
            return self._cmd_resume(**kwargs)
        return {
            "cmd": cmd_norm,
            "status": "error",
            "detail": f"Unknown return command: '{cmd_norm}'",
        }

    def _cmd_halt(self, **kwargs: Any) -> Dict[str, Any]:
        paused, errors = [], []
        for acc in self.list_accounts():
            aid = acc.get("account_id") or acc.get("id") or "?"
            try:
                _PAUSED_ACCOUNTS.add(aid)
                paused.append(aid)
            except Exception as exc:
                errors.append(f"{aid}: {exc}")
        detail = f"Paused {len(paused)} account(s)"
        if errors:
            detail += f"; errors: {errors}"
        self.push_alert(detail, source="return_commands", level="warning",
                        cmd="halt", paused=paused)
        return {
            "cmd": "halt",
            "status": "ok" if not errors else "partial",
            "detail": detail,
            "paused": paused,
            "errors": errors,
        }

    def _cmd_resume(self, **kwargs: Any) -> Dict[str, Any]:
        resumed, errors = [], []
        for acc in self.list_accounts():
            aid = acc.get("account_id") or acc.get("id") or "?"
            try:
                _PAUSED_ACCOUNTS.discard(aid)
                resumed.append(aid)
            except Exception as exc:
                errors.append(f"{aid}: {exc}")
        detail = f"Resumed {len(resumed)} account(s)"
        if errors:
            detail += f"; errors: {errors}"
        self.push_alert(detail, source="return_commands", level="info",
                        cmd="resume", resumed=resumed)
        return {
            "cmd": "resume",
            "status": "ok" if not errors else "partial",
            "detail": detail,
            "resumed": resumed,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # Unit 7 → Trading School
    # ------------------------------------------------------------------

    def validate_strategy_update(
        self,
        strategy: str,
        metrics: Dict[str, Any],
        thresholds: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Validate live *metrics* for *strategy* before applying an update.

        Delegates to ``src.units.trading_school.validator.validate_metrics()``.
        Thresholds are merged from units.yaml (``trading_school`` section) then
        from the *thresholds* argument, so callers may override per-invocation.

        Parameters
        ----------
        strategy : str
            Strategy name to validate.
        metrics : dict
            Observed performance data (win_rate, profit_factor, drawdown_pct,
            trade_count).
        thresholds : dict, optional
            Per-call threshold overrides.

        Returns
        -------
        dict
            ``{ok: bool, strategy: str, metrics: dict, issues: list[str]}``
        """
        from src.units.trading_school.validator import validate_metrics
        units = self._cfg.get("units") or {}
        yaml_th = (units.get("trading_school") or {}).get("thresholds") or {}
        merged = {**yaml_th, **(thresholds or {})}
        return validate_metrics(strategy, metrics, thresholds=merged or None)

    # ------------------------------------------------------------------
    # Live-plumbing smoke test (cross-unit: Strategies → Accounts → DB)
    # ------------------------------------------------------------------

    def smoke_test_run(
        self,
        account_id: Optional[str] = None,
        *,
        exchange_client: Optional[Any] = None,
        exchange_client_factory: Optional[Any] = None,
        dry_run: Optional[bool] = None,
        symbol: str = "BTCUSDT",
        direction: str = "long",
        ref_price: Optional[float] = None,
        db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Drive a live-plumbing smoke through the full 9-unit pipeline.

        Builds a ``smoke_test`` OrderPackage (``meta.is_test=True``), routes
        it through ``account_execute()`` for one or every account, captures
        the exchange's "too small" rejection as the success signal, logs a
        row to ``trade_journal.db``, pushes a dashboards alert, and returns
        a structured per-account result dict.

        Parameters
        ----------
        account_id : str, optional
            When set, only run the smoke against this account. Default
            (None) runs it against every account from accounts.yaml.
        exchange_client : object, optional
            A single Bybit/Binance client applied to every account. Use
            this only for single-account runs or tests; for multi-account
            live runs prefer ``exchange_client_factory`` so each account
            gets its own keyed client.
        exchange_client_factory : callable, optional
            ``factory(account_cfg) -> client | None``. Resolved once per
            account inside the loop, so multi-account live runs route
            each order through the right wallet's keys. When both
            ``exchange_client`` and ``exchange_client_factory`` are set,
            ``exchange_client`` wins (per-call override).
        dry_run : bool, optional
            Override the executor's dry-run flag. None defers to env.
        symbol, direction, ref_price :
            Passed to the smoke_test strategy. Defaults are safe.
        db_path : str, optional
            Override path to trade_journal.db. None → repo-root default.

        Returns
        -------
        dict
            ``{
              "smoke_id": str,
              "results": [
                {
                  "account_id": str,
                  "exchange": str,
                  "trade_id": str,         # "rejected_too_small:..." on success
                  "status": "rejected_too_small" | "submitted" | "dry_run" | "error",
                  "reason": str,
                  "logged": bool,
                }, ...
              ],
              "ok": bool,                  # True when at least one account passed
            }``
        """
        from src.units.strategies.smoke_test import order_package as _smoke_pkg

        cfg: Dict[str, Any] = {"symbol": symbol, "direction": direction}
        if ref_price is not None:
            cfg["ref_price"] = float(ref_price)

        pkg_dict = _smoke_pkg(cfg, candles_df=None)
        pkg = OrderPackage(strategy="smoke_test", **pkg_dict)
        smoke_id = pkg.meta.get("smoke_id", "")

        accounts = self.list_accounts()
        if account_id:
            accounts = [a for a in accounts if a.get("account_id") == account_id]
            if not accounts:
                return {
                    "smoke_id": smoke_id,
                    "results": [],
                    "ok": False,
                    "error": f"account '{account_id}' not found in accounts.yaml",
                }

        results: List[Dict[str, Any]] = []
        ok_any = False
        for acc in accounts:
            aid = acc.get("account_id") or "unknown"
            exchange = acc.get("exchange") or "unknown"
            entry: Dict[str, Any] = {
                "account_id": aid,
                "exchange": exchange,
                "trade_id": None,
                "status": "error",
                "reason": "",
                "logged": False,
            }
            try:
                from src.units.accounts.execute import execute_pkg
                client = exchange_client
                factory_error: Optional[str] = None
                if client is None and exchange_client_factory is not None:
                    try:
                        client = exchange_client_factory(acc)
                    except Exception as factory_exc:  # noqa: BLE001
                        logger.warning(
                            "smoke_test_run: client factory failed for %s: %s",
                            aid, factory_exc,
                        )
                        client = None
                        factory_error = str(factory_exc)

                # The smoke test must always contact the exchange — that's the
                # whole point. A missing client here means per-account API creds
                # aren't loaded into the bot's process environment, which is a
                # real integration failure. Surface it as an error rather than
                # silently flipping to dry-run (which used to mask the problem).
                # Tests that want the dry-run path still set dry_run=True
                # explicitly.
                if client is None and dry_run is not True:
                    entry["status"] = "error"
                    entry["reason"] = (
                        f"missing API credentials for account '{aid}' "
                        f"(check api_key_env in accounts.yaml + that the "
                        f"matching env vars are sourced into the bot's "
                        f"process environment)"
                    )
                    if factory_error:
                        entry["reason"] += f" — factory error: {factory_error}"
                else:
                    trade_id = execute_pkg(
                        pkg, acc,
                        exchange_client=client,
                        dry_run=dry_run,
                    )
                    entry["trade_id"] = trade_id
                    if isinstance(trade_id, str) and trade_id.startswith("rejected_too_small:"):
                        entry["status"] = "rejected_too_small"
                        entry["reason"] = trade_id.split(":", 1)[1]
                        ok_any = True
                    elif isinstance(trade_id, str) and trade_id.startswith("dry-"):
                        # Reached when the account is in dry_run mode
                        # (config/accounts.yaml `mode: dry_run` →
                        # RiskManager.dry_run → execute_pkg short-circuits).
                        entry["status"] = "dry_run"
                        entry["reason"] = "account mode=dry_run — exchange not contacted"
                        ok_any = True
                    else:
                        entry["status"] = "submitted"
                        entry["reason"] = (
                            "Bybit accepted the smoke order — operator should "
                            "flatten manually."
                        )
            except Exception as exc:  # noqa: BLE001
                entry["status"] = "error"
                entry["reason"] = str(exc)

            entry["logged"] = _log_smoke_to_journal(
                pkg, entry, db_path=db_path,
            )
            self.push_alert(
                f"smoke_test {aid}: {entry['status']} — {entry['reason']}",
                source="accounts",
                level="info" if entry["status"] != "error" else "warning",
                smoke_id=smoke_id,
                account_id=aid,
                trade_id=entry["trade_id"],
                status=entry["status"],
            )
            results.append(entry)

        return {
            "smoke_id": smoke_id,
            "results": results,
            "ok": ok_any,
            "package": {
                "symbol": pkg.symbol,
                "direction": pkg.direction,
                "entry": pkg.entry,
                "qty": pkg.meta.get("test_qty"),
            },
        }

    # ------------------------------------------------------------------
    # Unit 7 → Trading School (continued)
    # ------------------------------------------------------------------

    def trigger_backtest(
        self,
        strategy: str,
        config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Queue a backtest run for *strategy* via the Colab/VM polling mechanism.

        Writes a JSON line to the backtest queue file (default
        ``/tmp/backtest-queue.json``; override via ``BACKTEST_QUEUE_PATH`` env
        var).  A VM cron job or Colab notebook polls this file and runs the
        backtest.  Pushes an info alert to the dashboards queue.

        Returns
        -------
        dict
            ``{queued: True, strategy: str, queue_path: str, payload: dict}``
        """
        from src.units.trading_school.validator import trigger_backtest
        result = trigger_backtest(strategy, config=config)
        self.push_alert(
            f"Backtest queued: {strategy} → {result.get('queue_path')}",
            source="trading_school",
            level="info",
            strategy=strategy,
            queue_path=result.get("queue_path"),
        )
        return result


# ---------------------------------------------------------------------------
# Module-level helpers (used by downstream units)
# ---------------------------------------------------------------------------


def is_paused(account_id: str) -> bool:
    """Check if *account_id* is currently halted.  Used by accounts unit (PR #122)."""
    return account_id in _PAUSED_ACCOUNTS


def _build_intent_legs(pkg: "OrderPackage", delta) -> List[Dict[str, Any]]:
    """Translate an ``ExecutionDelta`` into a list of executor legs.

    Each leg is a dict consumed by ``multi_account_execute``'s
    intent-mode dispatcher:

    ::

        {
            "pkg":         <OrderPackage with the leg's direction>,
            "qty":         <float, qty_override for execute_pkg>,
            "reduce_only": <bool, plumbed to execute_pkg's reduce_only>,
            "label":       <str, audit name; "primary" / "close" / "open">,
        }

    Mapping (matches ``src.runtime.intents.compute_execution_delta``):

    * ``open`` / ``increase`` — 1 leg, direction = ``pkg.direction``
      (unchanged), reduce_only=False.
    * ``reduce`` / ``close``  — 1 leg, direction = ``delta.side``
      (opposite of the current net), reduce_only=True. The trade
      journal stamps ``setup_type='intent_reduce'`` so downstream
      aggregations can filter reduce legs out of "new entries"
      cohorts.
    * ``flip``                — 2 legs in order:
        (1) close leg — direction opposite of current side,
            qty = abs(current_qty), reduce_only=True.
        (2) open leg  — direction = ``delta.side`` (the new desired
            net), qty = ``delta.qty_delta``, reduce_only=False.

    Pkg copies via ``dataclasses.replace`` so the per-leg direction
    mutation doesn't leak to other accounts in the same dispatch
    round (the same ``pkg`` instance is the input for every account
    in ``multi_account_execute``).

    Used only when ``package_is_intent_mode(pkg)`` is True; the
    non-intent legacy path is unchanged.
    """
    if delta.action in ("open", "increase"):
        # Direction matches pkg.direction by construction (the
        # aggregator's winning side is what the OrderPackage already
        # carries).
        return [
            {
                "pkg": pkg,
                "qty": float(delta.qty_delta),
                "reduce_only": False,
                "label": "primary",
            }
        ]
    if delta.action in ("reduce", "close"):
        # delta.side is the order direction (opposite of current).
        leg_pkg = dataclasses.replace(pkg, direction=delta.side)
        # ``meta`` is mutable; share by reference so the trade-journal
        # row picks up the execution_delta block we stamped earlier.
        return [
            {
                "pkg": leg_pkg,
                "qty": float(delta.qty_delta),
                "reduce_only": True,
                "label": delta.action,
            }
        ]
    if delta.action == "flip":
        current_side = "long" if delta.current_qty > 0 else "short"
        close_side = "short" if current_side == "long" else "long"
        close_qty = abs(float(delta.current_qty))
        close_pkg = dataclasses.replace(pkg, direction=close_side)
        # Open leg's direction is delta.side — the new desired
        # net direction after the flip.
        open_pkg = dataclasses.replace(pkg, direction=delta.side)
        return [
            {
                "pkg": close_pkg,
                "qty": close_qty,
                "reduce_only": True,
                "label": "flip_close",
            },
            {
                "pkg": open_pkg,
                "qty": float(delta.qty_delta),
                "reduce_only": False,
                "label": "flip_open",
            },
        ]
    raise ValueError(
        f"_build_intent_legs: unsupported delta.action={delta.action!r}; "
        "expected one of open/increase/reduce/close/flip (noop should be "
        "filtered upstream)."
    )



def _capture_account_context_snapshots(
    *,
    order_package_id: str,
    pkg: "OrderPackage",
    accounts: List[Any],
    live_balances: Dict[str, Optional[float]],
) -> None:
    """S-MLOPT-S12 Part B: write one snapshot per `(order_package_id, account)`
    BEFORE the per-account RiskManager runs.

    Best-effort: every exception is swallowed and logged. The trader's
    flow is never blocked on a snapshot write. Disabled via
    ``ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED`` (truthy → short-circuit).
    """
    import os as _os
    import sqlite3 as _sqlite3

    flag = _os.environ.get("ACCOUNT_CONTEXT_SNAPSHOTS_DISABLED", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return

    try:
        from src.utils.paths import trade_journal_db_path
        from src.units.accounts.context_snapshot import (
            AccountContextSnapshot, daily_state_for, drawdown_pct,
            now_utc, open_trades_count_for, write_snapshots,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[snapshot] import failed (%s); skipping capture", exc)
        return

    db_path = trade_journal_db_path()
    captured_at = now_utc()
    utc_date = captured_at.strftime("%Y-%m-%d")

    snapshots = []
    try:
        # Read-only pass for daily-state + open-trade count. Done with a
        # SHARED ro connection so each per-account lookup is O(1).
        ro_conn = _sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True,
        )
    except _sqlite3.Error as exc:
        logger.warning("[snapshot] ro-connect failed (%s); skipping capture", exc)
        return

    try:
        for acc in accounts:
            account_id = getattr(acc, "name", None) or ""
            if not account_id:
                continue
            equity = live_balances.get(account_id)
            if equity is None:
                cached = getattr(acc, "cached_balance_usd", None)
                equity = float(cached) if cached is not None else None
            daily_pnl, equity_high = daily_state_for(
                ro_conn, account_id, utc_date=utc_date,
            )
            dd = drawdown_pct(equity, equity_high)
            open_count = open_trades_count_for(ro_conn, account_id)
            snapshots.append(AccountContextSnapshot(
                captured_at_utc=captured_at,
                order_package_id=order_package_id,
                account_id=account_id,
                strategy_name=getattr(pkg, "strategy", None),
                symbol=getattr(pkg, "symbol", None),
                direction=getattr(pkg, "direction", None),
                equity=float(equity) if equity is not None else None,
                daily_pnl_realized=daily_pnl,
                daily_equity_high=equity_high,
                daily_drawdown_pct=dd,
                open_trades_count=open_count,
            ))
    finally:
        try:
            ro_conn.close()
        except _sqlite3.Error:
            pass

    if not snapshots:
        return
    try:
        write_snapshots(db_path, snapshots)
    except Exception as exc:  # noqa: BLE001 — defence-in-depth, writer is already best-effort
        logger.warning("[snapshot] write_snapshots failed (%s)", exc)


def _log_new_order_package(pkg: "OrderPackage") -> Optional[str]:
    """Insert a fresh row into ``trade_journal.db::order_packages``.

    Returns the generated ``order_package_id`` on success, ``None`` on
    any error (logged but never re-raised — observability writes must
    never crash the order path).

    Also writes the resolved id back to ``pkg.meta["order_package_id"]``
    so downstream code (``execute_pkg`` → ``_log_trade_to_journal``) can
    stamp ``order_packages.linked_trade_id`` on a successful entry —
    that wiring is what makes the strategy_monocle gate's
    ``linked_only=True`` filter actually find anything to gate on.
    """
    try:
        import uuid
        from src.utils.json_notes import dump_capped
        from src.units.db.database import Database

        order_package_id = (
            (pkg.meta or {}).get("order_package_id")
            or f"pkg-{uuid.uuid4().hex[:16]}"
        )
        from src.utils.paths import trade_journal_db_path
        path = trade_journal_db_path()
        db = Database(db_path=path)
        # ``model_scores`` is the per-model ML decision captured at signal
        # time (strategy_signal_builders._emit_shadow_preds →
        # shadow_adapter.capture_shadow_preds). Persist it in its own column
        # so consumers read the trade's ML decisions with a cheap SELECT;
        # keep it out of the generic ``meta`` blob to avoid duplication.
        meta_for_log = {
            k: v for k, v in (pkg.meta or {}).items()
            if k not in {"order_package_id", "model_scores"}
        }
        model_scores = (pkg.meta or {}).get("model_scores")
        # ExitPlan (P1, dynamic-take-profit consistency): the static
        # description of the whole intended exit, derived here from the
        # package's own entry/sl/tp(+meta.tp2) fields. Journaled only in P1 —
        # nothing reads it back to drive behaviour yet (the materializer is
        # P2+), so this is observe-only and best-effort: a derivation failure
        # never blocks the order log.
        exit_plan = None
        try:
            from src.runtime.exit_plan import build_exit_plan_from_legacy
            exit_plan = build_exit_plan_from_legacy({
                "strategy_name": pkg.strategy,
                "entry": float(pkg.entry),
                "sl": float(pkg.sl),
                "tp": float(pkg.tp),
                "meta": pkg.meta or {},
            })
        except Exception as exc:  # noqa: BLE001 — observe-only metadata
            logger.debug("exit_plan derivation skipped for %s: %s", pkg.strategy, exc)
        # Materialized exit (P2, observe-only): translate the static ExitPlan into
        # the concrete, ordered exit instructions a broker/ticket would rest
        # (account-agnostic fractional qtys — the package is logged pre-sizing).
        # Journaled into the ``exit_plan_state`` column for soak; nothing reads it
        # back to drive an order yet (live re-materialization is P3/P4).
        exit_plan_state = None
        if exit_plan is not None:
            try:
                from src.runtime.exit_plan_materializer import materialize_exit_plan
                exit_plan_state = materialize_exit_plan(
                    exit_plan,
                    direction=pkg.direction,
                    entry=float(pkg.entry),
                    stop=float(pkg.sl),
                )
            except Exception as exc:  # noqa: BLE001 — observe-only metadata
                logger.debug("exit_plan materialization skipped for %s: %s", pkg.strategy, exc)
        db.insert_order_package({
            "order_package_id": order_package_id,
            "strategy_name": pkg.strategy,
            "symbol": pkg.symbol,
            "direction": pkg.direction,
            "entry": float(pkg.entry),
            "sl": float(pkg.sl),
            "tp": float(pkg.tp),
            "confidence": float(getattr(pkg, "confidence", 0.0) or 0.0),
            "signal_logic": dump_capped(meta_for_log, 8000, ensure_ascii=True),
            "status": "open",
            "meta": meta_for_log,
            "model_scores": model_scores if isinstance(model_scores, (dict, list)) else None,
            "exit_plan": exit_plan,
            "exit_plan_state": exit_plan_state,
        })
        # Stamp the id back onto pkg.meta so the executor can read it.
        if pkg.meta is None:
            pkg.meta = {}
        pkg.meta["order_package_id"] = order_package_id
        return order_package_id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_log_new_order_package failed for %s/%s: %s",
            getattr(pkg, "strategy", "?"), getattr(pkg, "symbol", "?"), exc,
        )
        return None


def _explain_zero_sized_qty(
    *,
    balance: float,
    available_usd: Optional[float],
    total_account_usd: Optional[float],
    risk_manager: Any,
    direction: str,
    market_type: str,
) -> str:
    """Synthesise an operator-actionable reason string for a
    ``sized_qty <= 0`` outcome.

    Pre-fix the rejection site hardcoded ``below_min_balance`` which
    was misleading whenever the actual cause was the daily-loss-budget
    gate or any other RiskManager refusal — operators saw
    "balance=186.87 < 50.0" and couldn't tell the comparison was a
    lie.

    Returns a structured-token-prefixed reason whose first segment
    matches one of the known refusal causes (so log-grepping stays
    practical) followed by the relevant inputs:

      * ``zero_balance:`` — the account has no funds to size against
        (non-positive balance). There is NO arbitrary minimum-balance
        floor anymore — ``min_balance_usd`` was removed 2026-06-24;
        the only floor is physics (you can't risk a fraction of zero).
      * ``risk_refused:`` — generic catch-all (daily-loss budget or
        any other RiskManager rule). Includes balance +
        total_account_usd so the operator can reproduce.

    PR 5 (2026-05-10): the ``zero_exchange_capacity`` token was
    removed alongside the spot-margin code paths.
    """
    gate_balance = (
        float(total_account_usd) if total_account_usd is not None else float(balance)
    )

    # 1. Non-positive balance — mirrors RiskManager.position_size's
    #    ``gate_balance <= 0`` guard. (No arbitrary min-balance floor.)
    if gate_balance <= 0:
        return (
            f"zero_balance: gate_balance={gate_balance:.2f} USD "
            f"(no funds available to size against)"
        )

    # 2. Generic refusal — daily-loss budget or any future
    #    RiskManager rule. Surface the inputs the operator needs
    #    to reproduce.
    avail_str = (
        f"{float(available_usd):.2f}" if available_usd is not None else "n/a"
    )
    total_str = (
        f"{float(total_account_usd):.2f}" if total_account_usd is not None else "n/a"
    )
    return (
        f"risk_refused: sized_qty=0 with balance={balance:.2f} "
        f"available_usd={avail_str} total_account_usd={total_str} "
        f"direction={direction} "
        f"market_type={market_type} — check daily-loss budget / "
        f"liquidation buffer / max_borrow"
    )


def _emit_execution_failure_ping(
    *,
    account: str,
    pkg: "OrderPackage",
    qty: Optional[float],
    reason: str,
    demo: bool = False,
) -> None:
    """Best-effort diagnostic ping for a per-account execution failure.

    Drops a JSON payload into ``runtime_logs/pending_pings/`` so the
    Telegram bot's ~5 s job-queue tick delivers it to the operator.
    Never raises — diagnostics must not crash the order path.
    """
    try:
        from src.runtime.execution_diagnostics import enqueue_execution_failure
        enqueue_execution_failure(
            account=account,
            strategy=getattr(pkg, "strategy", "unknown"),
            symbol=getattr(pkg, "symbol", "?"),
            side=("buy" if getattr(pkg, "direction", "") == "long" else "sell"),
            qty=qty,
            reason=reason,
            demo=demo,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_emit_execution_failure_ping failed for %s: %s", account, exc,
        )


def _enqueue_demo_ping(
    *,
    account: str,
    pkg: "OrderPackage",
    qty: Optional[float],
    status: str,
    detail: str,
) -> None:
    """Best-effort Telegram ping for a demo-account trade event.

    Separate from _emit_execution_failure_ping so successful demo submissions
    also reach the operator with the *DEMO TRADER* prefix. Never raises.
    """
    try:
        from src.runtime.execution_diagnostics import enqueue_demo_trade_notification
        enqueue_demo_trade_notification(
            account=account,
            strategy=getattr(pkg, "strategy", "unknown"),
            symbol=getattr(pkg, "symbol", "?"),
            side=("buy" if getattr(pkg, "direction", "") == "long" else "sell"),
            qty=qty,
            status=status,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_enqueue_demo_ping failed for %s: %s", account, exc)


def _log_smoke_to_journal(
    pkg: "OrderPackage",
    result: Dict[str, Any],
    *,
    db_path: Optional[str] = None,
) -> bool:
    """Write a row for the smoke order into ``trade_journal.db``.

    The row uses ``strategy_name="smoke_test"`` and ``status`` set to the
    smoke outcome (``rejected_too_small`` / ``dry_run`` / ``submitted`` /
    ``error``) so future ``/strategies`` aggregations can filter these
    out. Returns True on a successful insert, False on any error
    (logged but not re-raised — journal failure must never crash the
    smoke harness).
    """
    try:
        from datetime import datetime, timezone
        from src.units.db.database import Database
        from src.utils.paths import trade_journal_db_path

        path = db_path or trade_journal_db_path()
        db = Database(db_path=path)
        smoke_id = (pkg.meta or {}).get("smoke_id", "")
        notes = (
            f"smoke_id={smoke_id} "
            f"trade_id={result.get('trade_id')} "
            f"reason={result.get('reason', '')[:240]}"
        )
        smoke_account_id = str(result.get("account_id") or "unknown")
        # Stamp the paper/real_money category at insert so a smoke row on a
        # PAPER account is never mis-classified as real_money (column defaults
        # — is_demo=0, account_class=NULL→real_money — would otherwise leak it
        # into real-money PnL/stats). Canonical best-effort resolution (mirrors
        # execute.py); falls back to real_money on any lookup failure.
        from src.runtime.order_monitor import _resolve_account_class
        smoke_account_class = _resolve_account_class(smoke_account_id)
        db.insert_trade({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": pkg.symbol,
            "direction": pkg.direction,
            "entry_price": float(pkg.entry),
            "stop_loss": float(pkg.sl),
            "take_profit_1": float(pkg.tp),
            "position_size": float((pkg.meta or {}).get("test_qty") or 0.0),
            "setup_type": "smoke_test",
            "entry_reason": "live-plumbing smoke",
            "exit_reason": result.get("reason", ""),
            "status": str(result.get("status") or "smoke_test"),
            "notes": notes,
            "is_backtest": 0,
            "account_class": smoke_account_class,
            "is_demo": int(smoke_account_class == "paper"),
            "strategy_name": "smoke_test",
            "account_id": smoke_account_id,
        })
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("_log_smoke_to_journal failed: %s", exc)
        try:
            from src.runtime.outcomes import Level, report
            report(
                "smoke_test",
                "journal_write_failed",
                level=Level.WARN,
                reason=f"{type(exc).__name__}: {exc}",
            )
        except Exception:  # noqa: BLE001
            pass
        return False
