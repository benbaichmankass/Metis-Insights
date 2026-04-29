"""Data loaders for the Telegram bot — see docs/TELEGRAM-SPEC.md (Sprint S-001).

Single source of truth for **dynamic** runtime data the bot needs to render
its 11 commands. Every loader catches its own exceptions and returns a
neutral fallback (``[]`` / ``None`` / ``"⚠️ unavailable"``); command
handlers never see exceptions originating outside their own rendering code.

This module is delivered incrementally:

* PR-B1 — account registry, strategies, trader services.
* PR-B2 (this PR) — DB readers (signals, backtests, logs).
* PR-B3 — exchange-aware account queries (balance, positions, last trade).

Account registry (PM decision §8.1): ``config/accounts.yaml`` (optional —
PyYAML is **not** in requirements.txt and S-001 forbids new deps, so this
branch is gracefully skipped if PyYAML is unavailable) plus ``<repo>/.env``
and ``<repo>/.env.<account_id>`` files.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_BASE_DIR, "..", ".."))

ACCOUNTS_YAML_PATH = os.path.join(REPO_ROOT, "config", "accounts.yaml")
LEGACY_LIVE_SERVICE = "ict-trader-live"
LEGACY_LIVE_ACCOUNT_ID = "live"
TRADER_SERVICE_PREFIX = "ict-trader-"

# Trade-journal DB resolution mirrors src/bot/telegram_query_bot.py.
_TJ_CANDIDATES = [
    os.environ.get("TRADE_JOURNAL_DB", ""),
    os.path.join(REPO_ROOT, "trade_journal.db"),
    os.path.join(_BASE_DIR, "trade_journal.db"),
]
TRADE_JOURNAL_DB = next((p for p in _TJ_CANDIDATES if p and os.path.exists(p)),
                       os.path.join(REPO_ROOT, "trade_journal.db"))

# Signals DB written by src/runtime/signal_writer.py (literal "data/trades.db").
_SIG_CANDIDATES = [
    os.environ.get("SIGNALS_DB", ""),
    os.path.join(REPO_ROOT, "data", "trades.db"),
    os.path.join(REPO_ROOT, "trade_journal.db"),  # legacy combined DB
]
SIGNALS_DB = next((p for p in _SIG_CANDIDATES if p and os.path.exists(p)),
                  os.path.join(REPO_ROOT, "data", "trades.db"))

# Strategy → signal_type substring mapping (filters /last5 and /status).
_STRATEGY_SIGNAL_PREFIXES: Dict[str, tuple] = {
    "ict": ("fvg", "ob", "ict"),
    "killzone": ("killzone", "trade_signal"),
    "vwap": ("vwap",),
    "breakout_confirmation": ("ml_breakout", "breakout"),
}


# -- Strategies / services ----------------------------------------------------

def list_live_strategies() -> List[str]:
    """Return ``STRATEGIES`` from src.runtime.pipeline; ``[]`` on import failure."""
    try:
        from src.runtime.pipeline import STRATEGIES  # type: ignore
        return list(STRATEGIES)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_live_strategies: %s", exc)
        return []


def list_trader_services(deploy_dir: Optional[str] = None) -> List[str]:
    """Return systemd unit names matching ``ict-trader-*.service`` in ``deploy/``."""
    if deploy_dir is None:
        deploy_dir = os.path.join(REPO_ROOT, "deploy")
    try:
        if not os.path.isdir(deploy_dir):
            return []
        units = []
        for name in sorted(os.listdir(deploy_dir)):
            if name.endswith(".service"):
                stem = name[: -len(".service")]
                if stem.startswith(TRADER_SERVICE_PREFIX):
                    units.append(stem)
        return units
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_trader_services: %s", exc)
        return []


# -- Accounts -----------------------------------------------------------------

_ENV_RE = re.compile(r"^\.env\.(?P<account_id>[A-Za-z0-9_\-]+)$")


def _exchange_from_env(env_path: str) -> str:
    try:
        with open(env_path, "r", encoding="utf-8", errors="replace") as fh:
            blob = fh.read().upper()
    except Exception:
        return "unknown"
    if "BYBIT_API_KEY" in blob:
        return "bybit"
    if "BINANCE_API_KEY" in blob:
        return "binance"
    return "unknown"


def _load_yaml_accounts() -> List[Dict[str, Any]]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    if not os.path.exists(ACCOUNTS_YAML_PATH):
        return []
    try:
        with open(ACCOUNTS_YAML_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_load_yaml_accounts: %s", exc)
        return []
    raw = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        aid = str(item.get("account_id") or item.get("id") or "").strip()
        if not aid:
            continue
        out.append({
            "account_id": aid,
            "exchange": str(item.get("exchange", "")).strip().lower() or "unknown",
            "env_path": str(item.get("env_path", "")).strip() or None,
            "service": str(item.get("service", "")).strip()
                       or f"{TRADER_SERVICE_PREFIX}{aid}",
            "strategies": list(item.get("strategies", [])) or list_live_strategies(),
            "source": "yaml",
        })
    return out


def _load_env_accounts(repo_root: Optional[str] = None) -> List[Dict[str, Any]]:
    """``.env`` (no suffix) → legacy single account ``live`` on ``ict-trader-live``;
    ``.env.<account_id>`` → one account each on ``ict-trader-<account_id>``."""
    repo_root = repo_root or REPO_ROOT
    if not os.path.isdir(repo_root):
        return []
    out: List[Dict[str, Any]] = []
    legacy = os.path.join(repo_root, ".env")
    if os.path.isfile(legacy):
        out.append({
            "account_id": LEGACY_LIVE_ACCOUNT_ID,
            "exchange": _exchange_from_env(legacy),
            "env_path": legacy,
            "service": LEGACY_LIVE_SERVICE,
            "strategies": list_live_strategies(),
            "source": "env",
        })
    try:
        entries = sorted(os.listdir(repo_root))
    except Exception as exc:  # noqa: BLE001
        logger.warning("_load_env_accounts: %s", exc)
        return out
    for name in entries:
        m = _ENV_RE.match(name)
        if not m:
            continue
        aid = m.group("account_id")
        if aid == LEGACY_LIVE_ACCOUNT_ID:
            continue
        env_path = os.path.join(repo_root, name)
        out.append({
            "account_id": aid,
            "exchange": _exchange_from_env(env_path),
            "env_path": env_path,
            "service": f"{TRADER_SERVICE_PREFIX}{aid}",
            "strategies": list_live_strategies(),
            "source": "env",
        })
    return out


def list_accounts() -> List[Dict[str, Any]]:
    """YAML entries first (if PyYAML installed), then ``.env`` discovery.
    Deduplicated by ``account_id`` (first wins). Each dict has: ``account_id``,
    ``exchange``, ``env_path``, ``service``, ``strategies``, ``source``."""
    try:
        out, seen = [], set()
        for acc in _load_yaml_accounts() + _load_env_accounts():
            aid = acc["account_id"]
            if aid in seen:
                continue
            seen.add(aid)
            out.append(acc)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_accounts: %s", exc)
        return []


# -- Signals ------------------------------------------------------------------

def recent_signals_for(strategy: str, n: int = 5) -> List[Dict[str, Any]]:
    """Last ``n`` signals attributed to ``strategy`` via signal_type substring
    matching (see ``_STRATEGY_SIGNAL_PREFIXES``). Falls through to "any
    signal_type" when the strategy is unknown. Returns ``[]`` on any failure.
    """
    if not strategy:
        return []
    try:
        n = max(1, int(n))
    except (TypeError, ValueError):
        n = 5
    if not os.path.exists(SIGNALS_DB):
        return []
    try:
        prefixes = _STRATEGY_SIGNAL_PREFIXES.get(strategy.lower())
        conn = sqlite3.connect(SIGNALS_DB)
        try:
            conn.row_factory = sqlite3.Row
            cols = ("id, timestamp, symbol, signal_type, direction, price,"
                    " timeframe, reason, metadata")
            if prefixes:
                where = " OR ".join(["signal_type LIKE ?"] * len(prefixes))
                params = [f"%{p}%" for p in prefixes] + [n]
                rows = conn.execute(
                    f"SELECT {cols} FROM signals WHERE {where} "
                    f"ORDER BY datetime(timestamp) DESC, id DESC LIMIT ?",
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {cols} FROM signals "
                    "ORDER BY datetime(timestamp) DESC, id DESC LIMIT ?",
                    (n,),
                ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent_signals_for(%s): %s", strategy, exc)
        return []


# -- Logs (journalctl wrapper) ------------------------------------------------

def _default_runner(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20)


def recent_logs_for(service: str, n: int = 20, *, _runner=None) -> str:
    """Last ``n`` journalctl lines for ``service`` (or ``"⚠️ unavailable"``).
    ``_runner`` is a test injection point — must accept a list argv and
    return an object exposing ``stdout`` and ``stderr``.
    """
    if not service or not isinstance(service, str):
        return "⚠️ unavailable"
    try:
        n = max(1, int(n))
    except (TypeError, ValueError):
        n = 20
    runner = _runner or _default_runner
    try:
        result = runner(["journalctl", "-u", service, "-n", str(n), "--no-pager"])
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        return out or f"No logs found for {service}."
    except FileNotFoundError:
        return "⚠️ unavailable"
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent_logs_for(%s): %s", service, exc)
        return "⚠️ unavailable"


# -- Backtests ----------------------------------------------------------------

def latest_backtests_per_model() -> List[Dict[str, Any]]:
    """Latest ``backtest_results`` row per ``strategy_version``. ``[]`` on failure."""
    if not os.path.exists(TRADE_JOURNAL_DB):
        return []
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT b.id, b.run_date, b.strategy_version, b.start_date,
                       b.end_date, b.total_trades, b.winning_trades,
                       b.losing_trades, b.win_rate, b.profit_factor,
                       b.expectancy, b.max_drawdown, b.max_drawdown_pct,
                       b.sharpe_ratio, b.total_pnl, b.total_pnl_pct,
                       b.avg_win, b.avg_loss, b.largest_win, b.largest_loss,
                       b.created_at
                FROM backtest_results b
                JOIN (
                    SELECT COALESCE(strategy_version, '') AS sv,
                           MAX(datetime(created_at)) AS latest
                    FROM backtest_results
                    GROUP BY COALESCE(strategy_version, '')
                ) m ON COALESCE(b.strategy_version, '') = m.sv
                   AND datetime(b.created_at) = m.latest
                ORDER BY b.strategy_version IS NULL, b.strategy_version ASC
                """
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("latest_backtests_per_model: %s", exc)
        return []
