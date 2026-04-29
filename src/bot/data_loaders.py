"""Data loaders for the Telegram bot — see docs/TELEGRAM-SPEC.md (Sprint S-001).

Single source of truth for **dynamic** runtime data the bot needs to render
its 11 commands. Every loader catches its own exceptions and returns a
neutral fallback (``[]`` / ``None`` / ``"⚠️ unavailable"``); command
handlers never see exceptions originating outside their own rendering code.

This module is delivered incrementally:

* PR-B1 — account registry, strategies, trader services.
* PR-B2 — DB readers (signals, backtests, logs).
* PR-B3 (this PR) — exchange-aware account queries (balance, positions,
  last trade).

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


# -- Exchange-aware account queries (PR-B3) -----------------------------------

def _read_env_file(env_path: str) -> Dict[str, str]:
    if not env_path or not os.path.exists(env_path):
        return {}
    try:
        from dotenv import dotenv_values  # type: ignore
        values = dotenv_values(env_path)
        return {k: v for k, v in values.items() if v is not None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("_read_env_file(%s): %s", env_path, exc)
        return {}


def _bybit_client(env_vars: Dict[str, str]):
    api_key = env_vars.get("BYBIT_API_KEY")
    api_secret = env_vars.get("BYBIT_API_SECRET")
    if not api_key or not api_secret:
        return None
    from pybit.unified_trading import HTTP  # type: ignore
    return HTTP(testnet=False, api_key=api_key, api_secret=api_secret)


def bybit_client_for(account: Dict[str, Any]):
    """Return a Bybit HTTP client for ``account``, or ``None`` if creds are missing."""
    if not isinstance(account, dict):
        return None
    env = _read_env_file(account.get("env_path") or "")
    return _bybit_client(env)


def _binance_conn(env_vars: Dict[str, str]):
    api_key = env_vars.get("BINANCE_API_KEY")
    api_secret = env_vars.get("BINANCE_API_SECRET")
    if not api_key or not api_secret:
        return None
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from exchange.binance_connector import BinanceConnector  # type: ignore
    testnet = str(env_vars.get("BINANCE_TESTNET", "false")).strip().lower() == "true"
    return BinanceConnector(api_key=api_key, api_secret=api_secret, testnet=testnet)


def _f(x, default=0.0):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return default


def account_balance(account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return ``{"total_usdt": float, "raw": ...}`` or ``None`` on failure."""
    if not isinstance(account, dict):
        return None
    env = _read_env_file(account.get("env_path") or "")
    ex = (account.get("exchange") or "unknown").lower()
    try:
        if ex == "bybit":
            client = _bybit_client(env)
            if client is None:
                return None
            resp = client.get_wallet_balance(accountType="UNIFIED")
            lst = resp.get("result", {}).get("list", []) if isinstance(resp, dict) else []
            total = sum(_f(c.get("usdValue")) for c in (lst[0].get("coin", []) if lst else []))
            return {"total_usdt": total, "raw": resp}
        if ex == "binance":
            conn = _binance_conn(env)
            if conn is None:
                return None
            bal = conn.get_balance() or {}
            usdt = bal.get("USDT", {}) if isinstance(bal, dict) else {}
            return {"total_usdt": _f((usdt or {}).get("total")), "raw": bal}
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_balance(%s): %s", account.get("account_id"), exc)
        return None


def account_open_positions(account: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Return list of ``{symbol, side, size, entry_price, unrealised_pnl}``
    dicts (size > 0). ``None`` on failure."""
    if not isinstance(account, dict):
        return None
    env = _read_env_file(account.get("env_path") or "")
    ex = (account.get("exchange") or "unknown").lower()
    try:
        if ex == "bybit":
            client = _bybit_client(env)
            if client is None:
                return None
            resp = client.get_positions(category="linear", settleCoin="USDT")
            raw = resp.get("result", {}).get("list", []) if isinstance(resp, dict) else []
            out = []
            for p in raw:
                size = _f(p.get("size"))
                if size <= 0:
                    continue
                out.append({"symbol": p.get("symbol"), "side": p.get("side"),
                            "size": size, "entry_price": _f(p.get("avgPrice")),
                            "unrealised_pnl": _f(p.get("unrealisedPnl"))})
            return out
        if ex == "binance":
            conn = _binance_conn(env)
            if conn is None:
                return None
            out = []
            for p in (conn.get_positions() or []):
                size = _f(p.get("contracts", p.get("positionAmt")))
                if size == 0:
                    continue
                out.append({"symbol": p.get("symbol"),
                            "side": p.get("side") or ("long" if size > 0 else "short"),
                            "size": abs(size), "entry_price": _f(p.get("entryPrice")),
                            "unrealised_pnl": _f(p.get("unrealizedPnl",
                                                       p.get("unrealised_pnl")))})
            return out
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_open_positions(%s): %s",
                       account.get("account_id"), exc)
        return None


def _count_signals_today(strategy: str) -> int:
    """Count today's signals in the signals DB attributed to *strategy*."""
    from datetime import date as _date
    if not os.path.exists(SIGNALS_DB):
        return 0
    today = _date.today().isoformat()
    prefixes = _STRATEGY_SIGNAL_PREFIXES.get(strategy.lower())
    try:
        conn = sqlite3.connect(SIGNALS_DB)
        try:
            if prefixes:
                where = " OR ".join(["signal_type LIKE ?"] * len(prefixes))
                row = conn.execute(
                    f"SELECT COUNT(*) FROM signals WHERE ({where}) AND DATE(timestamp) = ?",
                    [f"%{p}%" for p in prefixes] + [today],
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE DATE(timestamp) = ?",
                    (today,),
                ).fetchone()
        finally:
            conn.close()
        return int(row[0] or 0)
    except Exception as exc:
        logger.warning("_count_signals_today(%s): %s", strategy, exc)
        return 0


def _strategy_pnl_today(strategy_name: str) -> float:
    """Today's closed PnL for *strategy_name* from the trade journal."""
    from datetime import date as _date
    if not os.path.exists(TRADE_JOURNAL_DB):
        return 0.0
    today = _date.today().isoformat()
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades "
                "WHERE strategy_name = ? AND is_backtest = 0 AND status = 'closed' "
                "AND DATE(timestamp) = ?",
                (strategy_name, today),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0] or 0.0)
    except Exception as exc:
        logger.warning("_strategy_pnl_today(%s): %s", strategy_name, exc)
        return 0.0


def _strategy_open_positions(strategy_name: str) -> int:
    """Count of open, non-backtest trades for *strategy_name* in the trade journal."""
    if not os.path.exists(TRADE_JOURNAL_DB):
        return 0
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE strategy_name = ? AND status = 'open' AND is_backtest = 0",
                (strategy_name,),
            ).fetchone()
        finally:
            conn.close()
        return int(row[0] or 0)
    except Exception as exc:
        logger.warning("_strategy_open_positions(%s): %s", strategy_name, exc)
        return 0


def strategy_dashboard_data(strategies: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Return one dashboard dict per strategy.

    Keys: ``strategy``, ``signals_today``, ``pnl``, ``open_pos``, ``status``.
    Status is ``"active"`` for all strategies in the live STRATEGIES list.
    All counters fall back to zero when the DB is missing or the
    ``strategy_name`` column does not yet exist.
    """
    if strategies is None:
        strategies = list_live_strategies() or [
            "breakout_confirmation", "vwap", "killzone", "ict"
        ]
    rows = []
    for s in strategies:
        rows.append({
            "strategy": s,
            "signals_today": _count_signals_today(s),
            "pnl": _strategy_pnl_today(s),
            "open_pos": _strategy_open_positions(s),
            "status": "active",
        })
    return rows


def close_all_bybit_positions_for_strategy(
    account: Dict[str, Any], strategy_name: str
) -> Optional[str]:
    """Close all Bybit positions for *account* if it runs *strategy_name*.

    Returns a status string when the account runs the strategy (even if
    there were no positions), or ``None`` when the account's strategy list
    does not include *strategy_name* (caller should skip it).

    The strategy membership check is case-insensitive.
    """
    strategies = account.get("strategies") or []
    if strategy_name.lower() not in [s.lower() for s in strategies]:
        return None

    aid = account.get("account_id", "?")
    client = bybit_client_for(account)
    if client is None:
        return f"⚠️ {aid}: Bybit credentials not found."

    try:
        resp = client.get_positions(category="linear", settleCoin="USDT")
        positions = [
            p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0
        ]
        if not positions:
            return f"🟢 {aid}: No open positions to close."
        closed_count = 0
        errors: list = []
        for p in positions:
            try:
                side = "Sell" if p["side"] == "Buy" else "Buy"
                client.place_order(
                    category="linear", symbol=p["symbol"], side=side,
                    orderType="Market", qty=p["size"], reduceOnly=True,
                )
                closed_count += 1
            except Exception as e:
                errors.append(f"{p['symbol']}: {e}")
        msg = (
            f"🚨 *{aid} CLOSE {strategy_name.upper()}*\n\n"
            f"✅ Closed {closed_count} position(s)\n"
        )
        if errors:
            msg += f"❌ Failed: {len(errors)}\nErrors:\n" + "\n".join(errors[:5])
        return msg
    except Exception as exc:
        logger.warning("close_all_bybit_positions_for_strategy(%s, %s): %s",
                       aid, strategy_name, exc)
        return f"⚠️ {aid}: Error fetching positions: {exc}"


def account_last_trade(account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Most-recent live trade row from the trade-journal DB for ``account``.

    Returns ``None`` when the account has no live trades yet, the DB is
    missing, or any error occurs.
    """
    if not isinstance(account, dict):
        return None
    if not os.path.exists(TRADE_JOURNAL_DB):
        return None
    aid = account.get("account_id", LEGACY_LIVE_ACCOUNT_ID)
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, timestamp, symbol, direction, entry_price, exit_price,"
                " pnl, status, strategy_name, created_at FROM trades"
                " WHERE account_id = ? AND COALESCE(is_backtest, 0) = 0"
                " ORDER BY datetime(created_at) DESC, id DESC LIMIT 1",
                (aid,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_last_trade(%s): %s", account.get("account_id"), exc)
        return None


def recent_trades_for(account: Dict[str, Any], n: int = 5) -> List[Dict[str, Any]]:
    """Last ``n`` trade-journal rows for ``account`` (newest first).

    Returns dicts with the full set of columns the bot's ``/last5`` handler
    expects: id, timestamp, symbol, direction, entry/exit price,
    stop_loss, take_profit_1/2/3, position_size, setup_type, killzone,
    bias, entry_reason, exit_reason, pnl, pnl_percent, status, notes,
    is_backtest, created_at.

    Returns ``[]`` when the account has no trades yet, the DB is missing,
    or any error occurs.
    """
    if not isinstance(account, dict):
        return []
    if not os.path.exists(TRADE_JOURNAL_DB):
        return []
    aid = account.get("account_id", LEGACY_LIVE_ACCOUNT_ID)
    try:
        n = max(1, int(n))
    except (TypeError, ValueError):
        n = 5
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, symbol, direction, entry_price,"
                " exit_price, stop_loss, take_profit_1, take_profit_2,"
                " take_profit_3, position_size, setup_type, killzone, bias,"
                " entry_reason, exit_reason, pnl, pnl_percent, status, notes,"
                " is_backtest, created_at FROM trades"
                " WHERE account_id = ?"
                " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
                (aid, n),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent_trades_for(%s): %s", account.get("account_id"), exc)
        return []
