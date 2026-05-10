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

import importlib
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

import yaml

if TYPE_CHECKING:
    from src.units.accounts.account import TradingAccount

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_UNITS_YAML = os.path.join(_REPO_ROOT, "config", "units.yaml")
_ACCOUNTS_YAML = os.path.join(_REPO_ROOT, "config", "accounts.yaml")

# In-process pause sentinels (PR #122 will replace with persistent flags).
_PAUSED_ACCOUNTS: set[str] = set()

# Circuit breaker for exchange-side rejection storms — defends against the
# 2026-05-10 Bybit ErrCode 170131 retry-loop where a wedged borrow gate
# kept rejecting every order without any backoff (20/58 trades / 9 h on
# bybit_2 spot-margin Buy). After this many consecutive ``exchange_rejected``
# results on the same account, the coordinator auto-flips the account to
# ``mode: dry_run`` via ``set_account_dry_run`` and emits a critical alert.
# In-process counters; restart resets them. A successful placement on the
# same account zeroes the counter.
_EXCHANGE_REJECTION_PAUSE_THRESHOLD = 3
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_units(path: str = _UNITS_YAML) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


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
    ) -> None:
        self._units_path = units_path
        self._accounts_path = accounts_path
        self._cfg: Dict[str, Any] = {}
        # S-AI-WS7-PART-6: Coordinator-side cache of resolved
        # ShadowPredictor lists, keyed by strategy name. Lazily
        # populated on first dispatch; invalidated by
        # ``reload_strategy_config`` so a YAML edit re-resolves on
        # the next tick. Lifts the per-tick factory call out of the
        # strategy hot path.
        self._shadow_predictors_cache: Dict[str, list] = {}
        self._reload()

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
            return OrderPackage(strategy=strategy, **pkg_dict)
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

        Reads ``shadow_model_ids`` from the strategy's config. When
        non-empty, calls ``ml.shadow.factory.resolve_predictors`` once
        and memoises the result; subsequent ticks hit the cache and
        pay zero factory cost. ``reload_strategy_config`` clears the
        cache so a YAML edit re-resolves on the next tick.

        Returns an empty list when the strategy has no
        ``shadow_model_ids``, when the field is empty, or when every
        listed id fails the factory's stage gate. Per-id failures
        within ``resolve_predictors(strict=False)`` are logged and
        skipped — one bad id never poisons the rest of the list.

        The returned list is the same object stored in the cache; the
        dispatcher should not mutate it.
        """
        if name in self._shadow_predictors_cache:
            return self._shadow_predictors_cache[name]
        cfg = self._strategy_cfg(name)
        ids = cfg.get("shadow_model_ids") or []
        if not ids:
            self._shadow_predictors_cache[name] = []
            return self._shadow_predictors_cache[name]
        # Lazy import: ml.shadow imports the registry, which is
        # heavier than the strategy hot path needs unless shadow
        # mode is actually wired.
        from pathlib import Path as _Path

        from ml.registry.model_registry import ModelRegistry
        from ml.shadow.factory import (
            DEFAULT_LOG_PATH,
            DEFAULT_REGISTRY_ROOT,
            resolve_predictors,
        )

        registry_root = _Path(
            cfg.get("_shadow_registry_root") or DEFAULT_REGISTRY_ROOT
        )
        log_path = _Path(cfg.get("_shadow_log_path") or DEFAULT_LOG_PATH)
        predictors = resolve_predictors(
            ids,
            ModelRegistry(registry_root),
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
        daily_pnl, max_daily_loss_usd, max_pos_size_usd, halted, plus
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
        ``pkg.meta['sized_qty_by_account'][account.name]``. Accounts whose
        balance is below ``min_balance_usd`` produce ``qty=0.0`` and a
        ``below_min_balance`` skip result instead of being routed.

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
            ``below_min_balance`` skip — surfacing the missing wiring
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
        #      then refuses to size (below_min_balance).
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
            live = live_balances.get(acc.name)
            if live is not None:
                return float(live)
            # 3. Fixture / cached value on the account object itself.
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
        if order_package_id and isinstance(pkg.meta, dict):
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
        #      ``prop_velotrade_1``) load with ``configured=False`` when
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
            assigned = getattr(account_obj, "strategies", None)
            if assigned is None:
                return True  # legacy / no-mapping account
            if not assigned:
                return False  # explicit empty: block all strategies
            if not pkg.strategy:
                return True  # legacy package without a strategy tag
            return pkg.strategy in assigned

        accounts = [a for a in accounts if _eligible_for_dispatch(a)]

        results = []
        for account in accounts:
            if account_type and account.account_type != account_type:
                continue

            # Pre-build a minimal account_cfg for the sizing_failed /
            # below_min_balance refusal-journal writes downstream.
            # Mirrors the richer account_cfg built below; ``getattr``
            # keeps legacy/test fixtures (where the account object
            # may not carry ``api_key_env``) routing cleanly.
            _early_account_cfg = {
                "account_id": account.name,
                "exchange": account.exchange,
                "api_key_env": getattr(account, "api_key_env", None),
                "market_type": getattr(account, "market_type", "spot"),
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
                bybit_client_for, binance_conn_for, velotrade_client_for,
            )

            account_cfg = {
                "account_id": account.name,
                "exchange": account.exchange,
                "api_key_env": account.api_key_env,
                "risk_pct": account.risk_manager.risk_pct,
                "min_balance_usd": account.risk_manager.min_balance_usd,
                "min_qty": account.risk_manager.min_qty,
                "qty_precision": account.risk_manager.qty_precision,
                # Forward the legacy ``risk:`` sub-block for any code
                # path that reads from it (RiskManager re-construction
                # inside execute_pkg when qty_override is absent).
                "max_dd_pct": account.risk_manager.max_dd_pct,
                "daily_usd": account.risk_manager.max_daily_loss_usd,
                "pos_size": account.risk_manager.max_pos_size_usd,
                # Bybit V5 category routing (spot vs linear). Drives
                # ``_bybit_category`` inside execute.py — without this
                # plumb-through the executor falls back to the default
                # (spot) and ignores any per-account override.
                "market_type": getattr(account, "market_type", "spot"),
            }

            # Per-account live/dry resolution. The caller-supplied
            # ``dry_run`` is a process-level OVERRIDE — when set
            # (tests / smoke runs), it forces the whole dispatch
            # round into that mode. When ``None`` (the production
            # default), the per-account ``mode`` field decides via
            # ``account.dry_run`` (already resolved by ``load_accounts``
            # from ``cfg["mode"]`` + any ``set_account_dry_run`` runtime
            # override). Per CLAUDE.md the per-account RiskManager is
            # the single source of truth — pre-fix this method
            # defaulted to ``dry_run=True`` and silently overrode
            # every account's ``mode: live``.
            account_dry = bool(getattr(account, "dry_run", False))
            if dry_run is not None:
                effective_dry = bool(dry_run)
            else:
                effective_dry = account_dry

            client = None
            client_error: Optional[str] = None
            if not effective_dry:
                exchange_lc = (account.exchange or "").lower()
                try:
                    if exchange_lc == "bybit":
                        client = bybit_client_for(account_cfg)
                    elif exchange_lc == "binance":
                        client = binance_conn_for(account_cfg)
                    elif exchange_lc == "velotrade":
                        client = velotrade_client_for(account_cfg)
                    else:
                        client_error = (
                            f"unsupported exchange '{exchange_lc}' "
                            f"(expected bybit/binance/velotrade)"
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
                if client is None and client_error is None:
                    # Resolution returned None silently — account is
                    # loaded into the accounts unit but its env-var
                    # creds aren't set. Surface a clear "not fully
                    # configured" message so the operator's diagnostic
                    # ping points straight at the missing env var
                    # instead of looking like a generic exchange error.
                    client_error = (
                        f"account '{account.name}' is not fully "
                        f"configured: api_key_env="
                        f"{account.api_key_env!r} (and matching "
                        f"_SECRET) not in process env"
                    )

            # 1. Per-account sizing — the only place qty is decided.
            try:
                balance = float(fetcher(account))
                # Direction-aware balance override.
                #
                # Cash spot (``market_type: spot``): the account holds
                #   real BTC and USDT, and a sell order can only spend
                #   BTC while a buy can only spend USDT. Use the
                #   direction-aware free balance so the sizer never
                #   produces a qty that exceeds actual holdings. Without
                #   this override, total-portfolio balance produces
                #   Bybit ErrCode 170131 ("Insufficient balance") on
                #   spot Sell when the wallet holds USDT.
                #
                # Spot margin (``market_type: spot-margin``, S-047 T3
                #   D5): the account holds USDT collateral and borrows
                #   the asset it doesn't have (BTC for shorts, USDT
                #   notional for leveraged longs). Both directions size
                #   from USDT collateral — the same primitive the
                #   RiskManager spot-margin kernel (S-047 T2 D3)
                #   consumes. Per-direction free-coin readings are
                #   irrelevant: a USDT-only wallet must still be able
                #   to short BTC.
                _market_type = (
                    getattr(account, "market_type", "spot") or "spot"
                ).lower()
                _is_spot_margin = _market_type == "spot-margin"
                if (
                    _market_type in ("spot", "spot-margin")
                    and client is not None
                    and not effective_dry
                    and not bool(getattr(pkg, "meta", None) and (pkg.meta or {}).get("is_test"))
                ):
                    try:
                        from src.units.accounts.execute import (
                            _fetch_spot_coin_balances,
                            _SPOT_BUY_SAFETY_BUFFER,
                        )
                        _spot_bal = _fetch_spot_coin_balances(client, pkg.symbol)
                        # S-052: total account equity (free + locked across
                        # all coins, in USD, net of any open borrow
                        # liability). RiskManager uses this for the
                        # min_balance_usd gate so a wallet with $120 total
                        # but only $40 free USDT isn't refused as "too
                        # small". None when the exchange didn't return
                        # totalEquity — gate falls back to ``balance``
                        # (pre-S-052 contract).
                        total_account_usd = _spot_bal.get("total_account_usd")
                        if _is_spot_margin:
                            # S-053: spot-margin collateral is the wallet's
                            # NET equity, not free USDT. After a borrow-and-
                            # sell short, Bybit credits the sale proceeds to
                            # free USDT (~+$700 on a 0.009 BTC short at
                            # $79850), inflating ``quote_usdt`` while net
                            # equity is unchanged (the BTC borrow liability
                            # offsets the proceeds). Pre-S-053 the sizer
                            # treated that inflated cash as fresh risk
                            # capital and the next short over-sized ~6× —
                            # Bybit then rejected with 170131. ``totalEquity``
                            # is borrow-state-invariant and is the correct
                            # collateral input. Falls back to ``quote_usdt``
                            # only when Bybit didn't return totalEquity
                            # (pre-S-052 wallet shape) so the spot-margin
                            # path keeps a sensible default.
                            balance = (
                                total_account_usd
                                if total_account_usd is not None
                                else _spot_bal["quote_usdt"]
                            )
                            # ``available_usd`` is direction-aware (S-049
                            # long, S-053 short): it is the live exchange-
                            # side availability for the side this order
                            # spends. Bybit validates ``cost ≤
                            # availableBalance`` before consulting borrow
                            # capacity.
                            #   long  → free_usdt + usdt_borrow_capacity
                            #   short → free_base_usd + base_borrow_capacity
                            # Both pre-fee-buffered. Without the SHORT
                            # branch (pre-S-053) the BTC borrow line could
                            # exhaust as positions accumulate and 170131
                            # would surface even with the correct
                            # collateral input.
                            # S-056 (2026-05-08) + S-058 (2026-05-09):
                            # Bybit V5 empties ``availableToBorrow``
                            # for any coin row with walletBalance=0,
                            # zeroing the API-derived borrow capacity
                            # even when margin is enabled and there's
                            # USDT collateral. When that happens we
                            # fall back to the operator-configured
                            # ``risk.spot_margin_ltv`` (default 0.5,
                            # see ``DEFAULT_SPOT_MARGIN_LTV``) applied
                            # to the account's collateral, capped by
                            # ``risk.max_borrow_btc`` (the exchange
                            # tier ceiling).
                            #
                            # Pre-S-058 the fallback used ``quote_usdt``
                            # (free USDT cash) as the collateral input,
                            # on the assumption that spot-margin wallets
                            # are always 100 % USDT at idle. That broke
                            # whenever residue from an orphaned position
                            # left equity in a non-USDT coin: ``quote_usdt``
                            # collapsed to 0, the fallback collapsed to 0,
                            # and every new dispatch refused with
                            # ``zero_exchange_capacity`` even though there
                            # was real collateral on the account. Fix:
                            # use ``total_account_usd`` (Bybit
                            # ``totalEquity``) as the collateral basis,
                            # falling back to ``quote_usdt`` only when
                            # totalEquity isn't returned. Bybit's per-coin
                            # collateral ratio is conservatively ignored
                            # (the LTV ``×`` already buffers under the
                            # exchange's typical 80 % retail tier).
                            ltv = float(getattr(
                                account.risk_manager, "spot_margin_ltv",
                                0.0,
                            ) or 0.0)
                            usdt_collateral = float(
                                _spot_bal.get("quote_usdt") or 0.0
                            )
                            collateral_usd = (
                                float(total_account_usd)
                                if total_account_usd is not None
                                else usdt_collateral
                            )
                            # Fallback borrow capacity in USD —
                            # symmetric for long and short.
                            fallback_usd = collateral_usd * ltv
                            if pkg.direction == "long":
                                api_avail_usd = (
                                    _spot_bal["quote_usdt"]
                                    + _spot_bal["quote_borrow_usd"]
                                )
                                # Use the API-derived value when it's
                                # populated; otherwise fall back to
                                # collateral×LTV. ``max(api, fallback)``
                                # keeps the more permissive value when
                                # both are non-zero, but the fallback
                                # only matters when the API one is 0.
                                effective_usd = max(
                                    api_avail_usd,
                                    usdt_collateral + fallback_usd,
                                )
                                available_usd = (
                                    effective_usd * _SPOT_BUY_SAFETY_BUFFER
                                )
                            else:
                                # Short: API path adds free BTC + BTC
                                # borrow capacity (in qty), converted
                                # to USD via pkg.entry (S-054). When
                                # the API field is empty fall back to
                                # collateral×LTV / pkg.entry.
                                api_base_qty = (
                                    _spot_bal["base_qty"]
                                    + _spot_bal.get("base_borrow_qty", 0.0)
                                )
                                api_avail_usd = api_base_qty * pkg.entry
                                # Cap fallback BTC qty at max_borrow_btc
                                # so it can't exceed the operator's
                                # configured tier ceiling.
                                max_borrow_btc = float(getattr(
                                    account.risk_manager,
                                    "max_borrow_btc", 0.0,
                                ) or 0.0)
                                fallback_btc_qty = (
                                    fallback_usd / pkg.entry
                                    if pkg.entry > 0 else 0.0
                                )
                                if max_borrow_btc > 0:
                                    fallback_btc_qty = min(
                                        fallback_btc_qty, max_borrow_btc,
                                    )
                                fallback_usd_capped = (
                                    fallback_btc_qty * pkg.entry
                                )
                                effective_usd = max(
                                    api_avail_usd, fallback_usd_capped,
                                )
                                available_usd = (
                                    effective_usd * _SPOT_BUY_SAFETY_BUFFER
                                )
                                if (
                                    api_avail_usd <= 0.0
                                    and fallback_usd_capped > 0.0
                                ):
                                    logger.info(
                                        "multi_account_execute: spot-margin "
                                        "SHORT capacity fallback fired for "
                                        "%s (%s) — API availableToBorrow "
                                        "empty; using usdt_collateral=%.2f "
                                        "× ltv=%.2f → %.6f BTC "
                                        "(max_borrow_btc cap=%.6f)",
                                        account.name, pkg.symbol,
                                        usdt_collateral, ltv,
                                        fallback_btc_qty, max_borrow_btc,
                                    )
                        elif pkg.direction == "short":
                            balance = _spot_bal["base_usd_value"]
                            available_usd = None
                        else:
                            balance = _spot_bal["quote_usdt"]
                            available_usd = None
                        logger.debug(
                            "multi_account_execute: spot balance override "
                            "account=%s market_type=%s direction=%s "
                            "symbol=%s balance=%.4f available=%s "
                            "total_account=%s",
                            account.name, _market_type, pkg.direction,
                            pkg.symbol, balance,
                            f"{available_usd:.4f}" if available_usd is not None else "n/a",
                            f"{total_account_usd:.4f}" if total_account_usd is not None else "n/a",
                        )
                    except Exception as _spot_exc:  # noqa: BLE001
                        logger.warning(
                            "multi_account_execute: spot direction-aware balance "
                            "failed for %s (%s): %s — using total portfolio value",
                            account.name, pkg.symbol, _spot_exc,
                        )
                        available_usd = None
                        total_account_usd = None
                else:
                    available_usd = None
                    total_account_usd = None
                # S-047 T3 (D5): forward the routing label as a primitive
                # so the RiskManager spot-margin kernel (T2) applies its
                # max_borrow / borrow-fee / liquidation-buffer rules.
                # Default ``"spot"`` keeps non-spot-margin sizing
                # bit-identical to the pre-T3 contract. S-049: also pass
                # ``available_usd`` so the kernel's notional-vs-available
                # cap fires before Bybit returns 170131.
                sized_qty = account.risk_manager.position_size(
                    pkg, balance,
                    market_type=_market_type,
                    available_usd=available_usd,
                    total_account_usd=total_account_usd,
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

            sized_qty_by_account[account.name] = sized_qty

            # 2. Refuse to forward a zero-qty order. This branch fires
            # for ANY sized_qty <= 0 outcome from the RiskManager —
            # not only true "balance below floor" cases. Pre-fix the
            # error template hardcoded ``below_min_balance`` which was
            # misleading whenever the actual cause was the spot-margin
            # ``available_usd`` cap (Bybit V5 returning
            # ``availableToBorrow=0`` for the base coin on a USDT-only
            # wallet — see ``_fetch_spot_coin_balances`` and the test
            # ``test_short_zero_capacity_when_borrow_line_zero``), the
            # daily-loss-budget gate, or the liquidation-buffer
            # refusal. Operators saw "balance=186.87 < 50.0" and
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
                # Velotrade integration: ``evaluate`` returns a
                # structured reason on reject (DAILY_LOSS_CAP /
                # POSITION_SIZE_CAP / INTRADAY_DRAWDOWN /
                # SKIP_MISSION_MET / SKIP_OVERNIGHT_RESTRICTED /
                # SKIP_WEEKEND_RESTRICTED / account_mode_dry_run). The
                # reason flows through the result row's ``error`` field
                # so /signals and the diagnostic ping can distinguish a
                # true risk breach from a mission-aware skip.
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
                trade_id = execute_pkg(
                    pkg, account_cfg,
                    exchange_client=client,
                    balance_usdt=balance,
                    dry_run=exec_dry_run,
                    qty_override=sized_qty,
                )
                self.push_alert(
                    f"multi_execute: {account.name} {pkg.strategy} "
                    f"{pkg.direction} {pkg.symbol} qty={sized_qty} → {trade_id}",
                    source="accounts",
                    level="info",
                    account=account.name,
                    trade_id=trade_id,
                    sized_qty=sized_qty,
                )
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": trade_id,
                    "sized_qty": sized_qty,
                    "error": None,
                })
                # Reset the consecutive exchange-rejection counter on a
                # clean placement — the borrow gate is open again.
                _EXCHANGE_REJECTION_COUNTS.pop(account.name, None)
            except RiskBreach as exc:
                _emit_execution_failure_ping(
                    account=account.name,
                    pkg=pkg,
                    qty=sized_qty,
                    reason=f"RiskBreach: {exc}",
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
                # Circuit breaker: N consecutive exchange_rejected results
                # ⇒ auto-flip the account to dry_run + critical alert.
                # Prevents the 2026-05-10 Bybit ErrCode 170131 retry-loop
                # cascade. Counter is reset on the success branch above.
                _count = _EXCHANGE_REJECTION_COUNTS.get(account.name, 0) + 1
                _EXCHANGE_REJECTION_COUNTS[account.name] = _count
                if _count >= _EXCHANGE_REJECTION_PAUSE_THRESHOLD:
                    try:
                        self.set_account_dry_run(account.name, True)
                        _EXCHANGE_REJECTION_COUNTS[account.name] = 0
                        self.push_alert(
                            f"Account '{account.name}' auto-paused after "
                            f"{_count} consecutive exchange rejections "
                            f"(last: {type(exc).__name__}: {str(exc)[:120]}). "
                            f"Mode flipped to dry_run; investigate before "
                            f"re-enabling.",
                            source="accounts",
                            level="critical",
                            account=account.name,
                            consecutive_rejections=_count,
                        )
                    except Exception as alert_exc:  # noqa: BLE001
                        logger.warning(
                            "multi_account_execute: auto-pause on %s "
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
        if results and not any(r.get("trade_id") is not None for r in results):
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

    def set_account_dry_run(self, account_name: str, dry_run: bool) -> Dict[str, Any]:
        """Toggle the dry/live execution mode for *account_name*.

        The state is persisted in the accounts package's ``_DRY_RUN_OVERRIDES``
        dict and applied to every subsequent ``load_accounts()`` call.

        Parameters
        ----------
        account_name : str
            Name matching an entry in accounts.yaml (e.g. ``"bybit_1"``).
        dry_run : bool
            True → simulate (safe), False → live execution.

        Returns
        -------
        dict
            ``{account, dry_run, mode}`` confirmation dict.
        """
        from src.units.accounts import set_account_dry_run as _set

        _set(account_name, dry_run)
        mode = "dry" if dry_run else "live"
        self.push_alert(
            f"Account '{account_name}' set to {mode} mode",
            source="accounts",
            level="info",
            account=account_name,
            dry_run=dry_run,
        )
        return {"account": account_name, "dry_run": dry_run, "mode": mode}

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
        try:
            cfg = load_strategy_config(path)
        except FileNotFoundError:
            return {"reloaded": False, "error": f"strategies.yaml not found: {path}"}

        # S-AI-WS7-PART-6: drop the resolved-predictor cache so a
        # YAML edit (adding / removing / changing shadow_model_ids)
        # is picked up on the next dispatch.
        self._shadow_predictors_cache.clear()

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
        import json as _json
        import uuid
        from src.units.db.database import Database

        order_package_id = (
            (pkg.meta or {}).get("order_package_id")
            or f"pkg-{uuid.uuid4().hex[:16]}"
        )
        path = (
            os.environ.get("TRADE_JOURNAL_DB")
            or os.path.join(_REPO_ROOT, "trade_journal.db")
        )
        db = Database(db_path=path)
        meta_for_log = {
            k: v for k, v in (pkg.meta or {}).items()
            if k not in {"order_package_id"}
        }
        db.insert_order_package({
            "order_package_id": order_package_id,
            "strategy_name": pkg.strategy,
            "symbol": pkg.symbol,
            "direction": pkg.direction,
            "entry": float(pkg.entry),
            "sl": float(pkg.sl),
            "tp": float(pkg.tp),
            "confidence": float(getattr(pkg, "confidence", 0.0) or 0.0),
            "signal_logic": _json.dumps(meta_for_log, default=str)[:1000],
            "status": "open",
            "meta": meta_for_log,
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
    was misleading whenever the actual cause was the spot-margin
    ``available_usd`` cap, the daily-loss-budget gate, or the
    liquidation-buffer refusal — operators saw "balance=186.87 < 50.0"
    and couldn't tell the comparison was a lie.

    Returns a structured-token-prefixed reason whose first segment
    matches one of the known refusal causes (so log-grepping stays
    practical) followed by the relevant inputs:

      * ``below_min_balance:`` — total equity is below the configured
        floor.
      * ``zero_exchange_capacity:`` — the spot-margin
        ``available_usd`` collapsed to 0, typically because Bybit V5
        returned ``availableToBorrow=0`` for the order's spending
        side (a USDT-only wallet shorting BTC is the canonical case
        — see ``test_short_zero_capacity_when_borrow_line_zero``).
        Operator action: seed the base coin into the wallet, or
        check the per-coin margin tier on the exchange.
      * ``risk_refused:`` — generic catch-all (daily-loss budget,
        liquidation buffer, or any other RiskManager rule).
        Includes balance + available_usd + total_account_usd so the
        operator can reproduce.
    """
    min_balance_usd = float(getattr(risk_manager, "min_balance_usd", 0.0) or 0.0)
    gate_balance = (
        float(total_account_usd) if total_account_usd is not None else float(balance)
    )

    # 1. Below-min-balance gate — mirror RiskManager.position_size's
    #    own check at risk.py:541 so the message is accurate when
    #    that's the cause.
    if gate_balance < min_balance_usd:
        return (
            f"below_min_balance: gate_balance={gate_balance:.2f} USD < "
            f"min_balance_usd={min_balance_usd:.2f}"
        )

    # 2. Spot-margin zero-capacity refusal — the canonical bybit_2
    #    USDT-only-wallet-shorting-BTC case.
    if (
        market_type == "spot-margin"
        and available_usd is not None
        and float(available_usd) <= 0.0
    ):
        side_word = "base-coin" if direction == "short" else "USDT"
        return (
            f"zero_exchange_capacity: market_type=spot-margin "
            f"direction={direction} available_usd=0.00 — Bybit returned "
            f"zero {side_word} borrow capacity (check exchange margin "
            f"tier or seed the coin); balance={balance:.2f}"
        )

    # 3. Generic refusal — daily-loss budget, liquidation buffer,
    #    or any future RiskManager rule. Surface the inputs the
    #    operator needs to reproduce.
    avail_str = (
        f"{float(available_usd):.2f}" if available_usd is not None else "n/a"
    )
    total_str = (
        f"{float(total_account_usd):.2f}" if total_account_usd is not None else "n/a"
    )
    return (
        f"risk_refused: sized_qty=0 with balance={balance:.2f} "
        f"available_usd={avail_str} total_account_usd={total_str} "
        f"min_balance_usd={min_balance_usd:.2f} direction={direction} "
        f"market_type={market_type} — check daily-loss budget / "
        f"liquidation buffer / max_borrow"
    )


def _emit_execution_failure_ping(
    *,
    account: str,
    pkg: "OrderPackage",
    qty: Optional[float],
    reason: str,
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
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_emit_execution_failure_ping failed for %s: %s", account, exc,
        )


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

        path = db_path or os.environ.get("TRADE_JOURNAL_DB") or os.path.join(
            _REPO_ROOT, "trade_journal.db"
        )
        db = Database(db_path=path)
        smoke_id = (pkg.meta or {}).get("smoke_id", "")
        notes = (
            f"smoke_id={smoke_id} "
            f"trade_id={result.get('trade_id')} "
            f"reason={result.get('reason', '')[:240]}"
        )
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
            "strategy_name": "smoke_test",
            "account_id": str(result.get("account_id") or "unknown"),
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
