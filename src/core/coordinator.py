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
  Dashboards.stats() ◀────────────────┘
                                       │
  ReturnCommands.halt() ───────────────┘
"""
from __future__ import annotations

import importlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_UNITS_YAML = os.path.join(_REPO_ROOT, "config", "units.yaml")
_ACCOUNTS_YAML = os.path.join(_REPO_ROOT, "config", "accounts.yaml")

# In-process pause sentinels (PR #122 will replace with persistent flags).
_PAUSED_ACCOUNTS: set[str] = set()


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
                raise AttributeError(f"module has no order_package()")
            cfg = {**self._strategy_cfg(strategy), "symbol": symbol}
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
            Explicit dry-run override; defaults to DRY_RUN env var.

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
            cfg = yaml_accounts.get(ta.name)
            if cfg is None:
                entry["live_balance_error"] = (
                    "account missing from data_loaders view of accounts.yaml"
                )
                out.append(entry)
                continue
            try:
                from src.bot.data_loaders import account_balance as _acct_bal
                bal = _acct_bal(cfg)
            except Exception as exc:  # noqa: BLE001
                bal = None
                entry["live_balance_error"] = f"API error: {exc}"
            if bal is None and entry["live_balance_error"] is None:
                # account_balance() returns None for both "no creds" and
                # "API call failed" — be explicit so the operator knows
                # what to fix.
                entry["live_balance_error"] = (
                    "balance unavailable (missing API creds or exchange "
                    "rejected the request)"
                )
            elif bal is not None:
                entry["live_balance_usdt"] = float(bal.get("total_usdt") or 0.0)
            out.append(entry)
        return out

    def multi_account_execute(
        self,
        pkg: OrderPackage,
        accounts_path: Optional[str] = None,
        *,
        dry_run: bool = True,
        account_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Execute *pkg* on all accounts loaded from accounts.yaml.

        Parameters
        ----------
        pkg : OrderPackage
            The order package from strategy_order_pkg().
        accounts_path : str, optional
            Override path to accounts.yaml.
        dry_run : bool
            When True (default), simulate — no live exchange calls.
        account_type : str, optional
            When set, only execute on accounts matching this type
            (``"regular"`` | ``"prop"``).

        Returns
        -------
        list[dict]
            One result dict per account:
            ``{name, exchange, account_type, trade_id, error}``
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

        results = []
        for account in accounts:
            if account_type and account.account_type != account_type:
                continue
            try:
                trade_id = account.place_order(pkg, dry_run=dry_run)
                self.push_alert(
                    f"multi_execute: {account.name} {pkg.strategy} "
                    f"{pkg.direction} {pkg.symbol} → {trade_id}",
                    source="accounts",
                    level="info",
                    account=account.name,
                    trade_id=trade_id,
                )
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": trade_id,
                    "error": None,
                })
            except RiskBreach as exc:
                results.append({
                    "name": account.name,
                    "exchange": account.exchange,
                    "account_type": account.account_type,
                    "trade_id": None,
                    "error": str(exc),
                })
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
                        # Only reachable when dry_run=True was passed
                        # explicitly (test path).
                        entry["status"] = "dry_run"
                        entry["reason"] = "DRY_RUN — exchange not contacted"
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
        from src.data_layer.database import Database

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
