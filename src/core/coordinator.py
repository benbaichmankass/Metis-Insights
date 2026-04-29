"""TRANSLATOR / Coordinator — S-008 PR #120.

Central routing layer between the 9 units defined in config/units.yaml.
No unit communicates with another unit directly; all cross-unit data flows
through this class.

Unit interface stubs (filled in by subsequent PRs):
  PR #121 → strategy_order_pkg()   (strategies expose order_package())
  PR #122 → account_execute()      (accounts expose execute_pkg())

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

    def __init__(self, units_path: str = _UNITS_YAML) -> None:
        self._units_path = units_path
        self._cfg: Dict[str, Any] = {}
        self._reload()

    def _reload(self) -> None:
        try:
            self._cfg = _load_units(self._units_path)
        except FileNotFoundError:
            logger.warning("units.yaml not found at %s; using empty config", self._units_path)
            self._cfg = {}

    # ------------------------------------------------------------------
    # Unit 1 → Strategies
    # ------------------------------------------------------------------

    def strategy_order_pkg(self, strategy: str, symbol: str = "BTCUSDT") -> OrderPackage:
        """Generate an OrderPackage from *strategy*.

        Delegates to ``src.units.strategies.<strategy>.order_package(cfg)``.
        Raises NotImplementedError until PR #121 wires the strategy modules.
        """
        try:
            mod = importlib.import_module(f"src.units.strategies.{strategy}")
            if not hasattr(mod, "order_package"):
                raise AttributeError(f"module has no order_package()")
            pkg_dict = mod.order_package(self._strategy_cfg(strategy))
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

    def account_execute(self, account_id: str, pkg: OrderPackage) -> str:
        """Risk-check *pkg* then execute on *account_id*.  Returns a trade_id.

        Raises NotImplementedError until PR #122 wires the account modules.
        """
        raise NotImplementedError(
            f"Account execution for '{account_id}' not yet wired; "
            "implement in PR #122."
        )

    def list_accounts(self) -> List[Dict[str, Any]]:
        """Return account configs from units.yaml, falling back to data_loaders."""
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
        try:
            from src.bot.data_loaders import list_accounts as _dl_accounts
            return _dl_accounts()
        except Exception as exc:
            logger.warning("list_accounts fallback failed: %s", exc)
            return []

    def is_account_paused(self, account_id: str) -> bool:
        """Return True when *account_id* has been halted via return_command."""
        return account_id in _PAUSED_ACCOUNTS

    # ------------------------------------------------------------------
    # Unit 3 → Dashboards
    # ------------------------------------------------------------------

    def dashboard_stats(self) -> Dict[str, Any]:
        """Return unified stats for all strategies and accounts.

        Keys: ``strategies`` (list of per-strategy dicts),
              ``accounts`` (list of account configs).
        """
        from src.bot.data_loaders import strategy_dashboard_data
        strategies = strategy_dashboard_data()
        accounts = self.list_accounts()
        return {
            "strategies": strategies,
            "accounts": accounts,
        }

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
        return {
            "cmd": "resume",
            "status": "ok" if not errors else "partial",
            "detail": detail,
            "resumed": resumed,
            "errors": errors,
        }


# ---------------------------------------------------------------------------
# Module-level helpers (used by downstream units)
# ---------------------------------------------------------------------------


def is_paused(account_id: str) -> bool:
    """Check if *account_id* is currently halted.  Used by accounts unit (PR #122)."""
    return account_id in _PAUSED_ACCOUNTS
