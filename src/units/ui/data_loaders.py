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
from src.utils.paths import repo_root as _repo_root, data_dir as _data_dir  # noqa: E402
from src.utils.paths import trade_journal_db_path as _trade_journal_db_path  # noqa: E402
REPO_ROOT = _repo_root()

ACCOUNTS_YAML_PATH = os.path.join(REPO_ROOT, "config", "accounts.yaml")
LEGACY_LIVE_SERVICE = "ict-trader-live"
LEGACY_LIVE_ACCOUNT_ID = "live"
TRADER_SERVICE_PREFIX = "ict-trader-"

# Trade-journal DB resolution mirrors src/bot/telegram_query_bot.py:
# the canonical resolver first (TRADE_JOURNAL_DB env →
# $DATA_DIR/trade_journal.db → repo-root), then repo root as the
# existence-check fallback. The legacy ``src/bot/trade_journal.db``
# candidate was dropped — it was a stray duplicate journal we are
# eliminating (see src/utils/paths.py::trade_journal_db_path docstring).
_TJ_CANDIDATES = [
    _trade_journal_db_path(),
    os.path.join(REPO_ROOT, "trade_journal.db"),
]
TRADE_JOURNAL_DB = next((p for p in _TJ_CANDIDATES if p and os.path.exists(p)),
                       _trade_journal_db_path())

# Signals DB written by src/runtime/signal_writer.py via data_dir()/"trades.db".
# On the live VM DATA_DIR=/data/bot-data so the canonical path is
# /data/bot-data/data/trades.db — NOT <repo>/data/trades.db.
# The trade_journal.db fallback was removed: it has an incompatible signals
# schema (logged_at_utc column, not timestamp) that causes the warning
# "_count_signals_today: no such column: timestamp".
_SIG_CANDIDATES = [
    os.environ.get("SIGNALS_DB", ""),
    str(_data_dir() / "trades.db"),          # canonical: DATA_DIR/data/trades.db
    os.path.join(REPO_ROOT, "data", "trades.db"),  # legacy repo-relative fallback
]
SIGNALS_DB = next((p for p in _SIG_CANDIDATES if p and os.path.exists(p)),
                  str(_data_dir() / "trades.db"))

# Strategy → signal_type substring mapping (filters /last5 and /status).
# Used as a fallback when the registry is unavailable (S-007).
_STRATEGY_SIGNAL_PREFIXES_FALLBACK: Dict[str, tuple] = {
    "ict": ("fvg", "ob", "ict"),
    "killzone": ("killzone", "trade_signal"),
    "vwap": ("vwap",),
    "breakout_confirmation": ("ml_breakout", "breakout"),
}


def _get_signal_prefixes(strategy: str) -> tuple:
    """Return signal_type substrings for *strategy* from the registry.

    Falls back to the hardcoded map when the registry is unavailable so
    existing attribution behaviour is preserved in minimal environments.
    """
    try:
        from src.strategy_registry import signal_prefixes as _reg_sp  # type: ignore
        prefixes = _reg_sp(strategy.lower())
        if prefixes:
            return tuple(prefixes)
    except Exception as exc:
        logger.debug("_get_signal_prefixes: registry unavailable (%s)", exc)
    return _STRATEGY_SIGNAL_PREFIXES_FALLBACK.get(strategy.lower(), ())


# -- Strategies / services ----------------------------------------------------

def list_live_strategies() -> List[str]:
    """Return strategy names from the YAML registry (S-007).

    Falls back to importing ``STRATEGIES`` from pipeline if the registry is
    unavailable, and to ``[]`` if both fail.
    """
    try:
        from src.strategy_registry import load_strategies  # type: ignore
        return [s["name"] for s in load_strategies()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_live_strategies: registry unavailable (%s), trying pipeline", exc)
    try:
        from src.runtime.pipeline import STRATEGIES  # type: ignore
        return list(STRATEGIES)
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_live_strategies: pipeline fallback also failed: %s", exc)
        return []


def list_trader_services(deploy_dir: Optional[str] = None) -> List[str]:
    """Return systemd service stems for all registered strategies (S-007).

    Primary source: ``service`` field from ``config/strategies.yaml`` via the
    registry.  Falls back to scanning ``deploy/`` for ``ict-trader-*.service``
    unit files when the registry is unavailable.
    """
    try:
        from src.strategy_registry import load_strategies  # type: ignore
        # S-012 PR C4: single-process — every strategy maps to
        # ict-trader-live. Dedupe to return one entry per real service.
        return list(dict.fromkeys(s["service"] for s in load_strategies()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_trader_services: registry unavailable (%s), scanning deploy/", exc)

    # Legacy fallback: scan deploy/ directory for unit files.
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
        logger.warning("list_trader_services: deploy/ scan failed: %s", exc)
        return []


# -- Accounts -----------------------------------------------------------------

_ENV_RE = re.compile(r"^\.env\.(?P<account_id>[A-Za-z0-9_\-]+)$")

# S-012 PR D3 (phantom service investigation): account_id values that
# never represent real accounts. Filter them out of env discovery so the
# bot never sees phantom services like `ict-trader-example` or
# `ict-trader-bak` (the symptom that triggered S-012). Compare
# case-insensitively.
_ENV_DISCOVERY_RESERVED = {
    "example",         # .env.example — repo template
    "sample",          # .env.sample — alternate naming
    "template",        # .env.template
    "dist",            # .env.dist — packaging convention
    "default",         # .env.default
    "bak",             # .env.bak — backup file (the second phantom name)
    "backup",          # .env.backup
    "old",             # .env.old
    "orig",            # .env.orig
    "save",            # .env.save
    "test",            # .env.test — testing convention
    "tests",           # .env.tests
    "ci",              # .env.ci — CI override
    "local",           # .env.local — convention used by some frameworks
    "development",     # .env.development
    "dev",             # .env.dev
    "production",      # .env.production
    "prod",            # .env.prod
    "staging",         # .env.staging
}


def _exchange_from_env(env_path: str) -> str:
    try:
        with open(env_path, "r", encoding="utf-8", errors="replace") as fh:
            blob = fh.read().upper()
    except Exception:
        return "unknown"
    if "BYBIT_API_KEY" in blob:
        return "bybit"
    return "unknown"


def _load_yaml_accounts() -> List[Dict[str, Any]]:
    # Production shape (S-012 PR B3) is dict-keyed-by-account-id; the
    # canonical reader handles parse failures + missing-file + non-dict
    # shapes uniformly so every consumer of accounts.yaml stays in sync.
    # Legacy list-shape fixtures are no longer accepted — they were a
    # back-compat path for pre-S-012 tests and are not present in
    # any production YAML.
    from src.config.accounts_loader import load_accounts_dict
    errors: List[Dict[str, Any]] = []
    raw_cfgs = load_accounts_dict(ACCOUNTS_YAML_PATH, errors=errors)
    for err in errors:
        err_msg = err.get("error", "")
        try:
            from src.runtime.outcomes import Level, report
            if "PyYAML" in err_msg:
                report(
                    "data_loaders",
                    "pyyaml_missing",
                    level=Level.WARN,
                    reason=err_msg,
                )
            else:
                report(
                    "data_loaders",
                    "accounts_yaml_read_failed",
                    level=Level.WARN,
                    reason=err_msg,
                    path=err.get("path", str(ACCOUNTS_YAML_PATH)),
                )
        except Exception:  # noqa: BLE001
            pass
    items = []
    for key, item in raw_cfgs.items():
        merged = dict(item)
        merged.setdefault("account_id", key)
        items.append(merged)

    out = []
    for item in items:
        aid = str(item.get("account_id") or item.get("id") or "").strip()
        if not aid:
            continue
        entry = {
            "account_id": aid,
            "exchange": str(item.get("exchange", "")).strip().lower() or "unknown",
            "env_path": str(item.get("env_path", "")).strip() or None,
            # S-012 PR D2 (single-process): default to LEGACY_LIVE_SERVICE
            # not f"{TRADER_SERVICE_PREFIX}{aid}". Per-account systemd
            # units do not exist post-S-012; every account routes through
            # ict-trader-live.
            "service": str(item.get("service", "")).strip() or LEGACY_LIVE_SERVICE,
            "strategies": list(item.get("strategies", [])) or list_live_strategies(),
            "source": "yaml",
        }
        # S-023 PR2: preserve credential-resolution fields. The bot's
        # bybit_client_for reads these directly from
        # the account dict — without preserving them here every account
        # silently fell through to the legacy env_path branch (which
        # doesn't exist for accounts.yaml-managed accounts), which was
        # the second contributing cause of "balance unavailable".
        #
        # IB connection fields (ib_host/ib_port/ib_account/ib_client_id)
        # + mode are the Interactive Brokers equivalent: ib_client_for /
        # ib_read_client_for read them straight off the account dict, and
        # the read path gates on `mode` so the dry live gateway is never
        # dialled. Omitting them here made every IB account fall through to
        # "unsupported"/"ib_port unset" in the hourly report + dashboard
        # (the read path was blind to IB even though execution was wired).
        for k in (
            "api_key_env", "api_secret_env", "type", "risk", "market_type",
            "demo", "mode", "symbols",
            "ib_host", "ib_port", "ib_account", "ib_client_id",
            # Alpaca/OANDA host selector (paper vs live) + optional base_url.
            # WITHOUT these the read path (balance / open positions) builds
            # the client against the PAPER host, so a LIVE account's live key
            # 401s ("request is not authorized") and the dashboard/app show
            # no balance. BL-20260628-ALPACA-LIVE-HOST.
            "alpaca_env", "base_url", "oanda_env",
        ):
            v = item.get(k)
            if v is not None:
                entry[k] = v
        out.append(entry)
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
        # S-012 PR D3: skip reserved env-file names (.env.example,
        # .env.bak, .env.template, …) so they never produce phantom
        # account_ids / service references. This is the repo-side root
        # cause of the ict-trader-example / ict-trader-bak symptom that
        # triggered S-012.
        if aid.lower() in _ENV_DISCOVERY_RESERVED:
            logger.debug(
                "_load_env_accounts: skipping reserved env file '.env.%s'",
                aid,
            )
            continue
        env_path = os.path.join(repo_root, name)
        out.append({
            "account_id": aid,
            "exchange": _exchange_from_env(env_path),
            "env_path": env_path,
            # S-012 PR D2 (single-process): every env-discovered account
            # also routes through ict-trader-live. Per-account .service
            # files do not exist; D3 adds the regression test that
            # surfaces phantom service references.
            "service": LEGACY_LIVE_SERVICE,
            "strategies": list_live_strategies(),
            "source": "env",
        })
    return out


def list_accounts() -> List[Dict[str, Any]]:
    """Return all configured accounts.

    S-012 PR B3 made config/accounts.yaml the single source of truth for
    account identity (PM § 8 #3). When YAML accounts are present, env
    discovery is skipped — otherwise the legacy ``.env`` file produces a
    duplicate ``account_id="live"`` entry alongside the YAML accounts
    (the "Breakout (live)" symptom in /status). Env discovery still runs
    when YAML is absent so older deployments keep working.

    Each dict has: ``account_id``, ``exchange``, ``env_path``,
    ``service``, ``strategies``, ``source``.
    """
    try:
        yaml_accounts = _load_yaml_accounts()
        if yaml_accounts:
            return yaml_accounts

        # Legacy fallback: only used when accounts.yaml is absent or empty.
        out, seen = [], set()
        for acc in _load_env_accounts():
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
    matching (registry-driven, S-007). Falls through to "any signal_type"
    when no prefixes are configured. Returns ``[]`` on any failure.
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
        prefixes = _get_signal_prefixes(strategy) or None
        conn = sqlite3.connect(SIGNALS_DB)
        try:
            conn.row_factory = sqlite3.Row
            from src.runtime.signal_notifications import ensure_signals_table as _est
            _est(conn)
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


def backtest_history_for(strategy_version: str, n: int = 5) -> List[Dict[str, Any]]:
    """Last ``n`` backtest_results rows for *strategy_version* (newest first).

    Powers the enhanced ``/latest_backtest <strategy> [N]`` view —
    operator can see if a strategy is improving or regressing across
    consecutive backtest runs (CP-2026-05-?-??). Returns ``[]`` when the
    DB is missing, the strategy has no history, or any error occurs.
    """
    if not strategy_version:
        return []
    if not os.path.exists(TRADE_JOURNAL_DB):
        return []
    try:
        n = max(1, int(n))
    except (TypeError, ValueError):
        n = 5
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, run_date, strategy_version, start_date, end_date,"
                " total_trades, winning_trades, losing_trades, win_rate,"
                " profit_factor, expectancy, max_drawdown, max_drawdown_pct,"
                " sharpe_ratio, total_pnl, total_pnl_pct, avg_win, avg_loss,"
                " largest_win, largest_loss, created_at FROM backtest_results"
                " WHERE strategy_version = ?"
                " ORDER BY datetime(created_at) DESC LIMIT ?",
                (strategy_version, n),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("backtest_history_for(%r, %d): %s",
                       strategy_version, n, exc)
        return []


def list_backtest_strategies() -> List[str]:
    """Distinct ``strategy_version`` values from ``backtest_results``.

    Used by ``/latest_backtest <unknown>`` to show the operator the
    available strategy_version names instead of an empty result.
    Returns ``[]`` on any error.
    """
    if not os.path.exists(TRADE_JOURNAL_DB):
        return []
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT DISTINCT strategy_version FROM backtest_results"
                " WHERE strategy_version IS NOT NULL"
                " AND TRIM(strategy_version) != ''"
                " ORDER BY strategy_version"
            ).fetchall()
        finally:
            conn.close()
        return [r["strategy_version"] for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_backtest_strategies: %s", exc)
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


# Per-account exchange-client construction now lives in the accounts unit
# (src/units/accounts/clients.py) so the canonical owner of "what creds
# belong to which account" is also the canonical owner of "how do I open
# a client for that account". Re-export here for back-compat with every
# existing call site (telegram_query_bot, coordinator, smoke tests).
from src.units.accounts.clients import (  # noqa: E402
    bybit_client_for,
)


def _f(x, default=0.0):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Credential diagnostics (S-023 PR2)
# ---------------------------------------------------------------------------


def credentials_check(account: Dict[str, Any]) -> Optional[str]:
    """Return a human-readable string naming missing creds, or ``None``
    when every expected env var is present and accounted for.

    Surfaced by ``/accounts_status`` and ``/balance`` so the operator
    sees exactly which env var is missing — replacing the previous
    generic *"missing API creds or exchange rejected the request"*
    message that conflated three different failure modes.

    Resolution order matches ``bybit_client_for``:
      1. ``api_key_env`` (accounts.yaml contract): require both the
         declared key var and the derived (or explicit) secret var.
      2. ``env_path`` (legacy): require the .env file to exist.
    """
    if not isinstance(account, dict):
        return "account config is not a mapping"
    api_key_env = account.get("api_key_env")
    if api_key_env:
        secret_env = (
            account.get("api_secret_env")
            or api_key_env.replace("_API_KEY", "_API_SECRET")
        )
        missing = [name for name in (api_key_env, secret_env)
                   if not os.environ.get(name)]
        if missing:
            return (
                f"missing env vars: {', '.join(missing)} "
                f"(declared in config/accounts.yaml; export them in the "
                f"systemd unit's EnvironmentFile, then restart the trader)"
            )
        return None
    env_path = account.get("env_path")
    if env_path:
        if not os.path.exists(env_path):
            return f"env_path does not exist: {env_path}"
        return None
    return (
        "no api_key_env (accounts.yaml) and no env_path (legacy .env) "
        "configured for this account"
    )


def _bybit_response_error(resp: Any) -> Optional[str]:
    """Return a human-readable error string when a Bybit response
    indicates failure, or ``None`` on success.

    Bybit returns 200 OK with retCode != 0 on failures (invalid key,
    expired, rate limit, etc.). Surfacing retCode + retMsg is the
    direct API response the operator asked for.
    """
    if not isinstance(resp, dict):
        return f"unexpected response shape: {type(resp).__name__}"
    ret_code = resp.get("retCode")
    if ret_code in (None, 0, "0"):
        return None
    ret_msg = str(resp.get("retMsg") or "(no retMsg)")[:200]
    return f"Bybit error retCode={ret_code}: {ret_msg}"


def _m15_client_balance_diagnostic(
    ex: str, aid: str, account: Dict[str, Any],
) -> Dict[str, Any]:
    """Balance diagnostic for the M15 brokers (OANDA / Alpaca).

    Same ``{status, total_usdt, raw, error}`` shape as the other
    branches of :func:`account_balance_with_diagnostic`. Both clients
    expose ``balance()`` returning USD (OANDA NAV / Alpaca equity) or
    ``None`` on any failure; their factories return ``None`` when the
    fixed-name env credentials are unset (BL-20260611-006).
    """
    try:
        if ex == "oanda":
            from src.units.accounts.clients import oanda_client_for as _factory
            _missing = "OANDA_API_TOKEN / OANDA_ACCOUNT_ID"
        else:
            from src.units.accounts.clients import alpaca_client_for as _factory
            _missing = "ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY"
        client = _factory(account)
        if client is None:
            return {"status": "missing_creds", "total_usdt": None,
                    "raw": None,
                    "error": f"{_missing} not in process env"}
        bal = client.balance()
    except Exception as exc:  # noqa: BLE001
        logger.warning("account_balance(%s): %s", aid, exc)
        err_str = f"{type(exc).__name__}: {exc}"
        try:
            from src.runtime.api_reporting import report_api_failure
            report_api_failure(
                exchange=ex, op="balance",
                account_id=str(aid), error=err_str, exception=exc,
            )
        except Exception:  # noqa: BLE001
            pass
        return {"status": "api_error", "total_usdt": None, "raw": None,
                "error": err_str}
    if bal is None:
        return {"status": "api_error", "total_usdt": None, "raw": None,
                "error": f"{ex} balance() returned None "
                         "(API error or credentials rejected — see logs)"}
    return {"status": "ok", "total_usdt": float(bal),
            "raw": {"balance": float(bal)}, "error": None}


def _ib_balance_diagnostic(account: Dict[str, Any], aid: str) -> Dict[str, Any]:
    """Balance diagnostic for an Interactive Brokers account.

    Returns the same ``{status, total_usdt, raw, error}`` shape as the
    Bybit branch of :func:`account_balance_with_diagnostic`.

    * A **dry-run** IB account (``ib_live``) is reported as ``dry_run``
      WITHOUT opening a socket — the live gateway is never dialled from the
      read path until promotion, mirroring the coordinator.
    * A **live** IB account (``ib_paper``) is read via a read-only,
      PID-salted clientId (:func:`ib_read_client_for`) so the probe never
      collides with the trader's execution socket. ``net_liquidation``
      (USD) is reported as ``total_usdt`` the same way Bybit's USD wallet
      value is, falling back to available funds.
    """
    mode = str(account.get("mode") or "live").lower()
    if mode != "live":
        return {"status": "dry_run", "total_usdt": None, "raw": None,
                "error": "account mode is dry_run — live gateway not read"}
    try:
        from src.units.accounts.clients import ib_read_client_for
        from src.units.accounts.ib_client import IBConnectionError
    except Exception as exc:  # noqa: BLE001
        return {"status": "api_error", "total_usdt": None, "raw": None,
                "error": f"IB client import failed: {type(exc).__name__}: {exc}"}

    client = ib_read_client_for(account)
    if client is None:
        return {"status": "api_error", "total_usdt": None, "raw": None,
                "error": "ib_client_for returned None (ib_port unset?)"}
    try:
        bal = client.balance() or {}
    except IBConnectionError as exc:
        # A down/evicted Gateway is an EXPECTED, recurring state (the IB
        # session is single-per-username — an operator login evicts it).
        # The coordinator resolves balances every tick through this path,
        # so emitting a WARN+ outcome / Telegram ping here would storm.
        # Fail quietly: the precise reason is logged + returned, and the
        # hourly digest surfaces it once per hour via api_ok=False. No
        # report_api_failure (unlike Bybit, whose failures are rarer and
        # actionable).
        err_str = str(exc)
        logger.warning("account_balance(%s): %s", aid, err_str)
        return {"status": "api_error", "total_usdt": None, "raw": None,
                "error": err_str}
    except Exception as exc:  # noqa: BLE001
        err_str = f"{type(exc).__name__}: {exc}"
        logger.warning("account_balance(%s): %s", aid, err_str)
        return {"status": "api_error", "total_usdt": None, "raw": None,
                "error": err_str}

    total = float(
        bal.get("net_liquidation")
        or bal.get("available_funds")
        or 0.0
    )
    return {"status": "ok", "total_usdt": total, "raw": bal, "error": None}


def account_balance_with_diagnostic(
    account: Dict[str, Any],
) -> Dict[str, Any]:
    """Return a structured status dict.

    Shape:
      {
        "status":     "ok" | "missing_creds" | "api_error" | "unsupported",
        "total_usdt": float | None,
        "raw":        dict | None,
        "error":      str | None,
      }

    The thin ``account_balance(account)`` wrapper preserves the legacy
    ``dict | None`` contract for read-only consumers (UI rendering,
    hourly report). Diagnostic-aware callers (``/accounts_status``)
    call this function directly so they can show the operator exactly
    what failed.
    """
    if not isinstance(account, dict):
        return {"status": "unsupported", "total_usdt": None, "raw": None,
                "error": "account config is not a mapping"}

    ex = (account.get("exchange") or "unknown").lower()
    aid = account.get("account_id") or "unknown"

    # Interactive Brokers has NO API keys — connection identity is
    # host/port/clientId/account, so credentials_check() does not apply
    # (it would wrongly report "no api_key_env configured"). Dispatch
    # before the cred check.
    if ex in ("interactive_brokers", "ib"):
        return _ib_balance_diagnostic(account, str(aid))

    # OANDA / Alpaca use FIXED env-name credentials (bearer token /
    # key-pair), not the per-account ``api_key_env`` pattern, so they
    # also dispatch before credentials_check() — the IB precedent.
    # BL-20260611-006: before these branches existed the function fell
    # through to "unsupported", the coordinator's live-balance cache
    # stayed empty for both M15 accounts, and the risk gate refused
    # every gold/ETF signal with gate_balance=0.00 (trade #2536).
    if ex in ("oanda", "alpaca"):
        return _m15_client_balance_diagnostic(ex, str(aid), account)

    # Step 1: cred presence check (no API call yet).
    cred_err = credentials_check(account)
    if cred_err is not None:
        return {"status": "missing_creds", "total_usdt": None, "raw": None,
                "error": cred_err}

    if ex == "bybit":
        try:
            client = bybit_client_for(account)
            if client is None:
                # Defensive: should not happen post-credentials_check, but
                # pybit may be uninstalled or some other init path failed.
                return {"status": "missing_creds", "total_usdt": None,
                        "raw": None,
                        "error": "bybit client could not be created "
                                 "(pybit missing or init failed)"}
            resp = client.get_wallet_balance(accountType="UNIFIED")
        except Exception as exc:  # noqa: BLE001
            logger.warning("account_balance(%s): %s", aid, exc)
            err_str = f"{type(exc).__name__}: {exc}"
            try:
                from src.runtime.api_reporting import report_api_failure
                report_api_failure(
                    exchange="bybit", op="get_wallet_balance",
                    account_id=str(aid), error=err_str, exception=exc,
                )
            except Exception:  # noqa: BLE001
                pass
            return {"status": "api_error", "total_usdt": None, "raw": None,
                    "error": err_str}

        api_err = _bybit_response_error(resp)
        if api_err:
            try:
                from src.runtime.api_reporting import report_api_failure
                report_api_failure(
                    exchange="bybit", op="get_wallet_balance",
                    account_id=str(aid), error=api_err, response=resp,
                )
            except Exception:  # noqa: BLE001
                pass
            return {"status": "api_error", "total_usdt": None, "raw": resp,
                    "error": api_err}
        lst = (resp.get("result") or {}).get("list") or []
        total = sum(_f(c.get("usdValue"))
                    for c in (lst[0].get("coin", []) if lst else []))
        return {"status": "ok", "total_usdt": total, "raw": resp,
                "error": None}

    return {"status": "unsupported", "total_usdt": None, "raw": None,
            "error": f"exchange '{ex}' is not supported by account_balance"}


def account_balance(account: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return ``{"total_usdt": float, "raw": ...}`` or ``None`` on failure.

    Backward-compatible wrapper around
    ``account_balance_with_diagnostic`` — preserves the legacy
    dict-or-None contract for callers that just want the number
    (UI rendering, hourly report). Use the diagnostic variant when
    you need the failure reason.
    """
    diag = account_balance_with_diagnostic(account)
    if diag["status"] != "ok":
        return None
    return {"total_usdt": diag["total_usdt"], "raw": diag["raw"]}


def account_open_positions(account: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    """Back-compat delegate — the canonical implementation now lives in
    ``src/units/accounts/clients.py`` (lifted by BUG-042 PR 1).

    Per-account exchange-state reads are the accounts unit's
    responsibility per CLAUDE.md § "Architecture rules" § 3; the UI
    unit must not reach across the unit boundary to read exchange
    state. This wrapper preserves the public symbol so existing
    callers (Telegram bot, dashboards) keep working unchanged while
    the upcoming reconciler (PR 2) imports the new location directly.
    """
    from src.units.accounts.clients import (
        account_open_positions as _accounts_account_open_positions,
    )
    return _accounts_account_open_positions(account)


def _count_signals_today(strategy: str) -> int:
    """Count today's signals in the signals DB attributed to *strategy*."""
    from datetime import date as _date
    if not os.path.exists(SIGNALS_DB):
        return 0
    today = _date.today().isoformat()
    prefixes = _get_signal_prefixes(strategy) or None
    try:
        conn = sqlite3.connect(SIGNALS_DB)
        try:
            from src.runtime.signal_notifications import ensure_signals_table as _est
            _est(conn)
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
    """Return one dashboard dict per strategy (S-007: enriched with registry info).

    Keys: ``strategy``, ``service``, ``model``, ``signals_today``, ``pnl``,
    ``open_pos``, ``status``.  Source of truth for strategy names is the
    registry; no hardcoded fallback list.
    """
    if strategies is None:
        strategies = list_live_strategies()

    try:
        from src.strategy_registry import load_strategies as _load  # type: ignore
        reg_by_name: Dict[str, Any] = {s["name"]: s for s in _load()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy_dashboard_data: registry unavailable (%s)", exc)
        reg_by_name = {}

    rows = []
    for s in strategies:
        reg = reg_by_name.get(s, {})
        rows.append({
            "strategy": s,
            # S-012 PR D2 (single-process): default to ict-trader-live
            # rather than f"ict-trader-{s}". Per-strategy services do not
            # exist post-S-012.
            "service": reg.get("service") or LEGACY_LIVE_SERVICE,
            "model": reg.get("model"),
            "signals_today": _count_signals_today(s),
            "pnl": _strategy_pnl_today(s),
            "open_pos": _strategy_open_positions(s),
            "status": "active",
        })
    return rows


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
            # Filter out refusal rows (status='rejected' from RiskManager
            # refusals + status='exchange_rejected' from _submit_order
            # failures) so the operator's "last trade" view stays focused
            # on real exchange submissions. The /packages command
            # surfaces refusals separately. (CP-2026-05-03-14.)
            row = conn.execute(
                "SELECT id, timestamp, symbol, direction, entry_price, exit_price,"
                " pnl, status, strategy_name, created_at FROM trades"
                " WHERE account_id = ? AND COALESCE(is_backtest, 0) = 0"
                " AND COALESCE(status, 'open')"
                " NOT IN ('rejected', 'exchange_rejected', 'rejected_too_small', 'orphaned')"
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
            # Filter out refusal rows so /last5 stays focused on real
            # exchange submissions (CP-2026-05-03-14). /packages surfaces
            # refusals separately for the operator who wants to see them.
            rows = conn.execute(
                "SELECT id, timestamp, symbol, direction, entry_price,"
                " exit_price, stop_loss, take_profit_1, take_profit_2,"
                " take_profit_3, position_size, setup_type, killzone, bias,"
                " entry_reason, exit_reason, pnl, pnl_percent, status, notes,"
                " is_backtest, created_at FROM trades"
                " WHERE account_id = ?"
                " AND COALESCE(status, 'open')"
                " NOT IN ('rejected', 'exchange_rejected', 'rejected_too_small', 'orphaned')"
                " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
                (aid, n),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent_trades_for(%s): %s", account.get("account_id"), exc)
        return []


# ---------------------------------------------------------------------------
# /packages — rejection rows + open-but-undispatched order packages.
# ---------------------------------------------------------------------------
#
# Inverse of the rejection-row filters listed in CP-2026-05-03-14. The
# operator's ``/last5`` / hourly summaries / liveness watchdog all
# *exclude* rejection rows so the success-path numbers stay clean;
# ``/packages`` is the dedicated surface that *includes* them so the
# operator can see "why isn't VWAP placing trades?" without an SSH +
# direct DB query.
#
# Three sibling status tokens identify a rejection row:
#   - ``rejected``           — RiskManager refusal (PR #357 / CP-14):
#     account_mode_dry_run, DAILY_LOSS_CAP, INTRADAY_DRAWDOWN.
#   - ``exchange_rejected``  — exchange-side error (PR #357 / CP-14):
#     Bybit retCode != 0, broker rejection, missing creds.
#   - ``rejected_too_small`` — pre-existing smoke-test status set by
#     ``scripts/smoke_test_trade.py`` when Bybit returns
#     ``ErrCode: 10001`` ("contracts below minimum"). These are
#     intentional plumbing checks; surfacing them in ``/last5`` is
#     noise, surfacing them in ``/packages`` is correct (they're
#     real rejections, just not signal-driven). Added to the family
#     post-CP-16 when the operator hit smoke-test pollution in the
#     /last5 view (PR follow-up).
#   - ``orphaned``           — pre-#357 ghost rows where
#     ``_log_trade_to_journal`` wrote ``status='open'`` *before* the
#     exchange call returned. If the exchange rejected (insufficient
#     balance, leverage cap, etc.), the row was orphaned with no
#     rejection-row counterpart. Backfilled by the one-shot
#     ``notebooks/operator/cleanup_ghost_trades.ipynb`` migration
#     (CP-17 follow-up). Going forward PR #357 prevents this shape
#     for new trades.

REFUSAL_STATUSES = (
    "rejected", "exchange_rejected", "rejected_too_small", "orphaned",
)


def recent_rejections(n: int = 10) -> List[Dict[str, Any]]:
    """Last ``n`` rejection rows from ``trade_journal.db::trades``.

    Includes BOTH ``status='rejected'`` (RiskManager refusals —
    ``account_mode_dry_run``, ``DAILY_LOSS_CAP``,
    ``INTRADAY_DRAWDOWN``) AND ``status='exchange_rejected'`` (Bybit
    retCode != 0, broker rejection,
    ``MissingCredentialsError``, ``RuntimeError("Account is paused …")``).

    Newest first. Returns ``[]`` on any error.
    """
    if not os.path.exists(TRADE_JOURNAL_DB):
        return []
    try:
        n = max(1, int(n))
    except (TypeError, ValueError):
        n = 10
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, symbol, direction, entry_price,"
                " stop_loss, take_profit_1, position_size, setup_type,"
                " entry_reason, status, notes, strategy_name, account_id,"
                " created_at FROM trades"
                " WHERE status IN ('rejected', 'exchange_rejected', 'rejected_too_small', 'orphaned')"
                " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
                (n,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent_rejections(%d): %s", n, exc)
        return []


def open_order_packages(n: int = 10) -> List[Dict[str, Any]]:
    """Last ``n`` order packages with ``status='open'`` and no linked trade.

    These are signals the strategy emitted whose dispatch never landed
    a trade row — typically because every routed account refused (see
    ``recent_rejections`` for the matching refusal rows). Newest by
    ``updated_at`` first.

    Returns ``[]`` on any error.
    """
    if not os.path.exists(TRADE_JOURNAL_DB):
        return []
    try:
        n = max(1, int(n))
    except (TypeError, ValueError):
        n = 10
    try:
        conn = sqlite3.connect(TRADE_JOURNAL_DB)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT order_package_id, strategy_name, symbol, direction,"
                " entry, sl, tp, confidence, status, linked_trade_id,"
                " close_reason, created_at, updated_at"
                " FROM order_packages"
                " WHERE status = 'open' AND linked_trade_id IS NULL"
                " ORDER BY datetime(updated_at) DESC,"
                " datetime(created_at) DESC LIMIT ?",
                (n,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("open_order_packages(%d): %s", n, exc)
        return []
