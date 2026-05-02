from src.runtime.signal_notifications import get_last_signals, format_signals
import json
import os
import logging
import re
import sqlite3
import asyncio
import sys
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv, dotenv_values
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import requests

# Sprint S-001 PR-C..F: route data access through the data_loaders facade.
# Sprint S-002 M2: migrated close_all_bybit_positions to (account: dict) and
# deleted get_bybit_client_from_env.
# Sprint S-002 M3: get_strategy_label is account-aware; load_account_env and
# format_target_options deleted.
from src.bot import data_loaders as dl
from src.bot.vm_runner import handle_vm_command, RunnerResult, MAX_PROMPT_CHARS

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
# DB_PATH: check env override, then repo root, then src/bot/ (legacy).
_DB_CANDIDATES = [
    os.environ.get("TRADE_JOURNAL_DB", ""),
    os.path.join(REPO_ROOT, "trade_journal.db"),
    os.path.join(BASE_DIR, "trade_journal.db"),
]
DB_PATH = next((p for p in _DB_CANDIDATES if p and os.path.exists(p)), os.path.join(REPO_ROOT, "trade_journal.db"))

# Fallback service name used when dl.list_accounts() returns no accounts.
# Multi-account deployments use per-account service keys from list_accounts().
LIVE_SERVICE_NAME = "ict-trader-live"

BACKTESTER_PATH = os.path.join(os.path.dirname(BASE_DIR), "backtest", "run_backtest.py")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HALT_FLAG_PATH = "/tmp/trader_halt.flag"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

# S-017 T1 — match what the live trader (src/main.py) already does:
# install the token-redacting filter on the root logger, and demote
# httpx/httpcore to WARNING so the bot token doesn't appear in plaintext
# in ``journalctl -u ict-telegram-bot``. python-telegram-bot uses httpx
# under the hood and httpx logs every outgoing URL at INFO. Operator-
# flagged in CP-2026-04-30-05; until S-017 only the trader process had
# this protection — the bot process leaked.
from src.utils.log_redact import install_redacting_filter, suppress_httpx_logging  # noqa: E402
install_redacting_filter()
suppress_httpx_logging()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Coordinator singleton (S-008 PR #124 — Telegram Bot rewired)
# ---------------------------------------------------------------------------
# All cross-unit data flows through the Coordinator (TRANSLATOR).  The bot
# is a pure consumer: it reads from dashboard_stats() / recent_signals() and
# writes return commands through return_command().
# ---------------------------------------------------------------------------

_coordinator = None


def get_coordinator():
    """Return the module-level Coordinator singleton (lazy-initialised)."""
    global _coordinator
    if _coordinator is None:
        try:
            from src.core.coordinator import Coordinator
            _coordinator = Coordinator()
        except Exception as exc:
            logger.warning("get_coordinator: failed to initialise Coordinator: %s", exc)
    return _coordinator


BACKTEST_TASK = None
BACKTEST_STATUS = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "last_stdout_tail": None,
    "last_returncode": None,
}


def is_authorised(update: Update) -> bool:
    if update.effective_chat:
        chat_id = update.effective_chat.id
    elif update.callback_query:
        chat_id = update.callback_query.message.chat.id
    else:
        return False
    return str(chat_id) == str(TELEGRAM_CHAT_ID)


def is_halted() -> bool:
    return os.path.exists(HALT_FLAG_PATH)


def fetch_today_pnl(account_id: str | None = None) -> tuple:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if account_id is not None:
            cur.execute(
                "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                "WHERE DATE(timestamp) = ? AND is_backtest = 0 AND account_id = ?",
                (today, account_id),
            )
        else:
            cur.execute(
                "SELECT COUNT(*), SUM(COALESCE(pnl, 0)) FROM trades "
                "WHERE DATE(timestamp) = ? AND is_backtest = 0",
                (today,),
            )
        row = cur.fetchone()
        conn.close()
        return (row[0] or 0, float(row[1] or 0.0))
    except Exception:
        return (0, 0.0)


def fetch_open_positions_count(account_id: str | None = None) -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        if account_id is not None:
            cur.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE status = 'open' AND is_backtest = 0 AND account_id = ?",
                (account_id,),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) FROM trades WHERE status = 'open' AND is_backtest = 0"
            )
        row = cur.fetchone()
        conn.close()
        return row[0] or 0
    except Exception:
        return 0


_STRATEGY_DISPLAY = {
    "killzone": "ICT",
    "ict": "ICT",
    "vwap": "VWAP",
    "breakout": "Breakout",
    "breakout_confirmation": "Breakout",
    "turtle_soup": "Turtle Soup",
    "multiplexed": "Multi",
}

# Default label when STRATEGY env var is missing or unrecognised. The bot is
# live-trading only; this fallback should rarely be visible.
_DEFAULT_STRATEGY_LABEL = "Strategy"


def get_strategy_label(account: dict | None = None) -> str:
    """Return the display name for the active strategy.

    Resolution order (S-012 PR hotfix — accounts.yaml strategies field):

      1. ``STRATEGY`` (or legacy ``STRATEGY_NAME``) in the account's
         .env file. Used by env-discovered accounts.
      2. ``account["strategies"]`` from accounts.yaml. When the account
         runs a single strategy, that name is shown; when it runs more
         than one (the post-S-012 multiplexer norm), label is "Multi".
      3. Fall back to the global ``STRATEGY`` env var.
      4. ``_DEFAULT_STRATEGY_LABEL``.

    Defensive against missing/malformed env files because this is called
    at ``post_init`` time and must never crash the bot.
    """
    try:
        if account is None:
            accounts = dl.list_accounts() or []
            account = accounts[0] if accounts else {}

        # 1. Per-account .env STRATEGY/STRATEGY_NAME
        env_vars = _account_env(account)
        raw = str(env_vars.get("STRATEGY", env_vars.get("STRATEGY_NAME", ""))).strip().lower()
        if raw:
            label = _STRATEGY_DISPLAY.get(raw)
            if label:
                return label

        # 2. accounts.yaml strategies list
        strategies = account.get("strategies") if isinstance(account, dict) else None
        if isinstance(strategies, list) and strategies:
            normalized = [str(s).strip().lower() for s in strategies if s]
            if len(normalized) == 1:
                label = _STRATEGY_DISPLAY.get(normalized[0])
                if label:
                    return label
            elif len(normalized) > 1:
                # Multi-strategy account → multiplexer label.
                return _STRATEGY_DISPLAY["multiplexed"]

        # 3. Process-wide STRATEGY env (the run_pipeline default since PR C5).
        proc_raw = str(os.environ.get("STRATEGY", "")).strip().lower()
        if proc_raw:
            label = _STRATEGY_DISPLAY.get(proc_raw)
            if label:
                return label

        return _DEFAULT_STRATEGY_LABEL
    except Exception:
        return _DEFAULT_STRATEGY_LABEL



# fetch_last_5_trades and fetch_latest_backtest_result were removed in PR-F
# (Sprint S-001). /last5 now reads via dl.recent_trades_for; /latest_backtest
# and the post-backtest broadcast read via dl.latest_backtests_per_model().


def format_backtest_summary(latest):
    return (
        f"✅ *Latest backtest result*\n"
        f"🆔 Row ID: {latest['id']}\n"
        f"🗓 Run Date: {latest['run_date']}\n"
        f"🏷 Strategy Version: {latest['strategy_version']}\n"
        f"📅 Period: {latest['start_date']} → {latest['end_date']}\n"
        f"🔢 Total Trades: {latest['total_trades']}\n"
        f"✅ Winners: {latest['winning_trades']}\n"
        f"❌ Losers: {latest['losing_trades']}\n"
        f"🎯 Win Rate: {latest['win_rate']}\n"
        f"⚖️ Profit Factor: {latest['profit_factor']}\n"
        f"📈 Expectancy: {latest['expectancy']}\n"
        f"📉 Max Drawdown: {latest['max_drawdown']}\n"
        f"📉 Max Drawdown %: {latest['max_drawdown_pct']}\n"
        f"📐 Sharpe Ratio: {latest['sharpe_ratio']}\n"
        f"💵 Total PnL: {latest['total_pnl']}\n"
        f"💹 Total PnL %: {latest['total_pnl_pct']}\n"
        f"🥇 Avg Win: {latest['avg_win']}\n"
        f"🥀 Avg Loss: {latest['avg_loss']}\n"
        f"🚀 Largest Win: {latest['largest_win']}\n"
        f"💥 Largest Loss: {latest['largest_loss']}\n"
        f"🕒 Saved At: {latest['created_at']}"
    )


def run_shell_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    return ((result.stdout or "") + (result.stderr or "")).strip()


def get_service_status(service_name: str) -> str:
    try:
        return run_shell_command(["systemctl", "is-active", service_name]) or "unknown"
    except Exception as e:
        return f"error: {e}"


def _known_systemd_units() -> set:
    """Return the set of systemd unit stems present in the repo's deploy/.

    Used by toggle_service() to fail loudly when callers pass a service
    name that has no matching unit file — the failure mode that
    triggered S-012 (PM § 8 #5).
    """
    deploy_dir = os.path.join(REPO_ROOT, "deploy")
    try:
        return {
            name[: -len(".service")]
            for name in os.listdir(deploy_dir)
            if name.endswith(".service")
        }
    except FileNotFoundError:
        return set()


def toggle_service(service_name: str, action: str) -> str:
    # S-012 PR D3: pre-validate against deploy/. If the unit file does
    # not exist in the repo, refuse to call systemctl rather than let
    # the operator see a confusing "Unit not found" error from systemd.
    known = _known_systemd_units()
    if known and service_name not in known:
        return (
            f"❌ Refusing to {action} `{service_name}`: no matching unit "
            f"file in deploy/. Known units: `{', '.join(sorted(known))}`. "
            "If this service should exist, add the unit file in a PR; "
            "otherwise fix the caller."
        )
    try:
        result = subprocess.run(
            ["sudo", "systemctl", action, service_name],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            new_status = get_service_status(service_name)
            return f"✅ `{service_name}` {action}ed. Status: `{new_status}`"
        err = (result.stderr or result.stdout or "unknown error").strip()
        return f"❌ Failed to {action} `{service_name}`:\n{err}"
    except Exception as e:
        return f"❌ Exception toggling `{service_name}`: {e}"


def get_last_logs(lines: int = 20) -> str:
    """Return the most recent journalctl lines for the live trader service.

    Thin wrapper kept for backwards-compat with any importers; new call sites
    should use ``dl.recent_logs_for(service, n=...)`` directly.
    """
    return dl.recent_logs_for(LIVE_SERVICE_NAME, n=lines)


def _account_env(account: dict) -> dict:
    """Best-effort load of the account's .env file (for strategy-label
    rendering). Empty dict on any failure — the caller's label fallback
    handles the “unknown” case."""
    path = (account or {}).get("env_path") or ""
    if not path or not os.path.exists(path):
        return {}
    try:
        return {k: v for k, v in dotenv_values(path).items() if v is not None}
    except Exception:  # noqa: BLE001
        return {}


def _bybit_creds_diagnostic(account: dict) -> str | None:
    """Return a diagnostic string when an account is missing Bybit creds.

    Returns ``None`` when both API key + secret env vars are present
    (in which case any balance failure is on the API side, not config).

    S-023 PR2: delegates to the shared
    ``data_loaders.credentials_check`` so /balance and
    /accounts_status give identical wording and stay in sync as the
    diagnostic logic evolves.
    """
    return dl.credentials_check(account or {})


def _account_balance_header(account: dict, *, exchange_suffix: str = "") -> str:
    """Build the balance block header.

    Account-first labeling (CP-2026-05-02): the primary identifier is the
    account_id (the thing the operator sees in accounts.yaml + the thing
    that actually owns the API key + wallet); the strategy is shown as a
    parenthetical so the operator knows which strategy routes through it
    without confusing strategies-have-balances. Two accounts that happen
    to share the same single-strategy assignment now show distinct
    headers — which exposes dup-key issues immediately.
    """
    aid = (account or {}).get("account_id", "?")
    strat = get_strategy_label(account)
    base = f"`{aid}`" + (f" ({strat})" if strat and strat != _DEFAULT_STRATEGY_LABEL else "")
    suffix = f" {exchange_suffix}" if exchange_suffix else ""
    return f"💰 *{base} Balance{suffix}*"


def format_bybit_balance(account: dict) -> str:
    """Render the per-coin Bybit balance block for one account.
    Data is sourced via ``dl.account_balance``; this function only formats."""
    header = _account_balance_header(account)
    payload = dl.account_balance(account)
    if payload is None:
        diag = _bybit_creds_diagnostic(account)
        suffix = f"\n→ {diag}" if diag else ""
        return f"{header}\n⚠️ Bybit error: balance unavailable.{suffix}"
    raw = (payload or {}).get("raw") or {}
    result_list = (raw.get("result") or {}).get("list") or []
    if not result_list:
        return f"{header}\nNo balance data returned from Bybit."
    coins = result_list[0].get("coin", []) or []
    lines = []
    for c in coins:
        try:
            wb = float(c.get("walletBalance", 0) or 0)
        except (TypeError, ValueError):
            wb = 0.0
        if wb <= 0:
            continue
        try:
            usd = float(c.get("usdValue", "0") or 0)
        except (TypeError, ValueError):
            usd = 0.0
        lines.append(f"{c.get('coin', '?')}: {wb:.4f} (≈ ${usd:.2f})")
    text = "\n".join(lines) if lines else "No non-zero balances found."
    return f"{header}\n{text}"


def format_bybit_positions(account: dict) -> str:
    """Render the open-positions block for one Bybit account using
    ``dl.account_open_positions`` output."""
    label = get_strategy_label(account)
    rows = dl.account_open_positions(account)
    if rows is None:
        return f"📊 *{label} Positions*\n⚠️ Bybit error: positions unavailable."
    if not rows:
        return f"📊 *{label} Positions*\nNo open positions."
    lines = []
    for p in rows:
        sym = p.get("symbol") or "?"
        side = p.get("side") or "?"
        size = p.get("size") or 0
        entry = float(p.get("entry_price") or 0)
        pnl = float(p.get("unrealised_pnl") or 0)
        lines.append(f"{sym} {side} | Size: {size} | Entry: ${entry:,.2f} | PnL: ${pnl:+.2f}")
    return f"📊 *{label} Positions*\n" + "\n".join(lines)


def close_all_bybit_positions(account: dict) -> str:
    aid = account.get("account_id", "?")
    client = dl.bybit_client_for(account)
    if client is None:
        return f"⚠️ {aid}: Bybit credentials not found."
    resp = client.get_positions(category="linear", settleCoin="USDT")
    positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
    if not positions:
        return f"🟢 {aid}: No open positions to close."
    closed_count = 0
    errors = []
    for p in positions:
        try:
            side = "Sell" if p["side"] == "Buy" else "Buy"
            client.place_order(
                category="linear", symbol=p["symbol"], side=side,
                orderType="Market", qty=p["size"], reduceOnly=True,
            )
            closed_count += 1
        except Exception as e:
            errors.append(f"{p['symbol']}: {str(e)}")
    msg = f"🚨 *{aid} CLOSE ALL*\n\n✅ Closed {closed_count} position(s)\n"
    if errors:
        msg += f"❌ Failed: {len(errors)}\nErrors:\n" + "\n".join(errors[:5])
    return msg


async def run_backtest_in_background(application: Application):
    global BACKTEST_TASK, BACKTEST_STATUS
    BACKTEST_STATUS.update({
        "state": "running",
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "finished_at": None, "last_error": None,
        "last_stdout_tail": None, "last_returncode": None,
    })
    try:
        if not os.path.exists(BACKTESTER_PATH):
            raise FileNotFoundError(f"backtester.py not found at: {BACKTESTER_PATH}")

        process = await asyncio.create_subprocess_exec(
            sys.executable, BACKTESTER_PATH,
            cwd=os.path.dirname(BACKTESTER_PATH),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
        stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace")

        BACKTEST_STATUS["last_returncode"] = process.returncode
        BACKTEST_STATUS["last_stdout_tail"] = (stdout_text or "")[-2000:]
        BACKTEST_STATUS["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        if process.returncode != 0:
            BACKTEST_STATUS["state"] = "failed"
            BACKTEST_STATUS["last_error"] = (stderr_text or stdout_text or "Unknown error")[-2000:]
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    f"⚠️ *Backtest failed*\n🕒 Finished: {BACKTEST_STATUS['finished_at']}\n"
                    f"🔢 Return code: {process.returncode}\n```{BACKTEST_STATUS['last_error']}```"
                ),
                parse_mode="Markdown",
            )
            return

        BACKTEST_STATUS["state"] = "completed"
        # PR-C: pull the freshest backtest row through data_loaders. The loader
        # returns one row per strategy_version; we surface the newest entry —
        # which matches the legacy single-row behaviour for today's pipeline.
        rows = dl.latest_backtests_per_model()
        latest = rows[0] if rows else None
        if latest:
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=format_backtest_summary(latest), parse_mode="Markdown"
            )
        else:
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    f"✅ *Backtest finished*\n🕒 {BACKTEST_STATUS['finished_at']}\n"
                    f"```{(stdout_text or 'No output')[-3000:]}```"
                ),
                parse_mode="Markdown",
            )
    except Exception as e:
        BACKTEST_STATUS["state"] = "failed"
        BACKTEST_STATUS["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        BACKTEST_STATUS["last_error"] = str(e)
        await application.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"⚠️ *Backtest crashed*\n`{str(e)}`",
            parse_mode="Markdown",
        )
    finally:
        BACKTEST_TASK = None



def format_binance_balance(account: dict) -> str:
    """Render the Binance Futures USDT balance block for one account.
    Total/free/used are derived from the loader's ``raw`` ccxt-style
    balance map (preserves today's UX)."""
    header = _account_balance_header(account, exchange_suffix="(Binance)")
    payload = dl.account_balance(account)
    if payload is None:
        return f"{header}\n⚠️ Error: balance unavailable."
    raw = (payload or {}).get("raw") or {}
    if not raw:
        return f"{header}\nNo data returned."
    usdt = raw.get("USDT", {}) if isinstance(raw, dict) else {}
    total = float((usdt or {}).get("total", 0) or 0)
    free = float((usdt or {}).get("free", 0) or 0)
    used = float((usdt or {}).get("used", 0) or 0)
    return (
        f"{header}\n"
        f"USDT Total: {total:.2f}\n"
        f"USDT Free: {free:.2f}\n"
        f"USDT Used: {used:.2f}"
    )


def format_binance_positions(account: dict) -> str:
    """Render the Binance open-positions block for one account using
    ``dl.account_open_positions`` output."""
    label = get_strategy_label(account)
    rows = dl.account_open_positions(account)
    if rows is None:
        return f"📊 *{label} Positions (Binance)*\n⚠️ Error: positions unavailable."
    if not rows:
        return f"📊 *{label} Positions (Binance)*\nNo open positions."
    lines = []
    for p in rows:
        sym = p.get("symbol") or "?"
        side = p.get("side") or "?"
        size = p.get("size") or 0
        entry = float(p.get("entry_price") or 0)
        pnl = float(p.get("unrealised_pnl") or 0)
        lines.append(f"{sym} {side} | Size: {size} | Entry: ${entry:,.2f} | PnL: ${pnl:+.2f}")
    return f"📊 *{label} Positions (Binance)*\n" + "\n".join(lines)


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    label = get_strategy_label()
    text = (
        f"👋 *ICT Trading Bot* — {label}\n\n"
        "*Live trading control*\n"
        "/status — Kill-switch state, P&L summary, service status\n"
        "/halt — Stop order placement immediately\n"
        "/resume — Re-enable order placement\n"
        "/closeall — Emergency close all positions\n"
        "/toggle — Start or stop the trader service\n\n"
        "*Account & strategy*\n"
        "/accounts — List accounts (dry/live + PnL) or toggle mode\n"
        "/accounts\\_status — Per-account risk state\n"
        "/risk\\_check <account> — Risk details for one account\n"
        "/smoke\\_test \\[account\\] — Live-plumbing smoke "
        "*\\(always LIVE — micro-qty rejected by exchange\\)*\n"
        "/strategies — Per-strategy signals, PnL and positions\n"
        "/reload\\_strats — Reload strategies.yaml without restart\n"
        "/balance — Account balance\n"
        "/trades — Open positions\n\n"
        "*Signals & history*\n"
        "/last5 — Last 5 journal entries\n"
        "/signals \\[N\\] \\[strategy\\] — Recent pipeline signals from audit log\n"
        "/alerts — Recent unit alerts (coordinator queue)\n"
        "/log — Recent trader logs\n"
        "/download\\_journal — Download trade journal DB\n"
        "/price — Current BTC price\n\n"
        "*Backtesting*\n"
        "/backtest — Start backtest in background\n"
        "/latest\\_backtest — Backtest status/result\n"
        "/backtest\\_ui — How to launch the Streamlit backtesting dashboard\n\n"
        "*Visibility*\n"
        "/health — Per-unit status + data-file freshness\n"
        "/vmstats — VM resource snapshot \\(uptime, load, mem, disk\\)\n\n"
        "*Web dashboard*\n"
        "/webapp — Open the secure web dashboard\n\n"
        "*VM-resident Claude (S-014.5)*\n"
        "/vm <prompt> — Tier 1 read-only Claude on the VM\n"
        "/vm\\_write <prompt> — Tier 2 mutating Claude on the VM \\(asks to confirm\\)\n\n"
        "*Sprint / planning*\n"
        "/checkpoint — Latest entry from CHECKPOINT\\_LOG.md\n"
        "/sprintlet\\_status \\[note\\] — Manual milestone update\n"
        "/sprintlet\\_complete \\[sprint\\] — Manual sprint-complete signal\n\n"
        "/help — Show this menu"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


# ---------------------------------------------------------------------------
# /set_keys — open the Colab key-rotation notebook
# ---------------------------------------------------------------------------

# Hardcoded so the message works even if the bot can't reach the repo.
# Update this constant if the repo or notebook path moves.
_COLAB_NOTEBOOK_URL = (
    "https://colab.research.google.com/github/the-lizardking/ict-trading-bot/"
    "blob/main/notebooks/operator/rotate_api_keys.ipynb"
)
_COLAB_DOC_URL = (
    "https://github.com/the-lizardking/ict-trading-bot/blob/main/"
    "docs/operator/colab-key-rotation.md"
)


async def cmd_set_keys(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply with the open-in-Colab link for the key-rotation notebook.

    The notebook reads from the operator's Colab Secrets and pushes a
    fresh ``.env.live`` to the VM. See
    ``docs/operator/colab-key-rotation.md`` for the full setup.
    """
    if not is_authorised(update):
        return
    msg = (
        "🔑 *Rotate API keys*\n\n"
        "Open in Colab:\n"
        f"{_COLAB_NOTEBOOK_URL}\n\n"
        "*Runtime → Run all*. The first run in a fresh session pops a "
        "one-click \"Allow Drive access\" dialog — click Allow and the "
        "rest is automatic.\n\n"
        f"Setup guide (one-time): {_COLAB_DOC_URL}\n\n"
        "Required Colab Secrets:\n"
        "• `BYBIT_API_KEY_1`, `BYBIT_API_SECRET_1`\n"
        "• `BYBIT_API_KEY_2`, `BYBIT_API_SECRET_2`\n"
        "• `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`\n"
        "• `VM_SSH_HOST`, `VM_SSH_USER`\n\n"
        "Required SSH key (in Google Drive):\n"
        "• Put your VM SSH private key in `My Drive/ICT_Bot_Secrets/` "
        "named `vm_ssh_key` (or `id_rsa` / `id_ed25519` / "
        "`ict-bot-ovm-private.key`).\n\n"
        "After Run all completes, run `/accounts_status` to verify."
    )
    await update.message.reply_text(
        msg, parse_mode="Markdown", disable_web_page_preview=True,
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    halted = is_halted()
    halt_line = "🔴 *HALTED* — orders blocked" if halted else "🟢 *RUNNING* — orders enabled"

    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []

    account_lines: list[str] = []
    for acc in accounts:
        aid = acc.get("account_id", "?")
        label = get_strategy_label(acc)
        trade_count, total_pnl = fetch_today_pnl(account_id=aid)
        open_count = fetch_open_positions_count(account_id=aid)
        # S-016 H1 (audit § A5): drop the systemd unit name from the
        # per-account block. Since S-012 every strategy runs inside the
        # single `ict-trader-live` unit, so the service name is identical
        # for every account and conveys no per-account info. The strategy
        # name (already in the bold header) is what the operator cares
        # about. The aggregate-level bot-status line below is unchanged.
        account_lines.append(
            f"*{label}* (`{aid}`)\n"
            f"  📊 Trades today: {trade_count} | P&L: ${total_pnl:+.2f}\n"
            f"  📂 Open (DB): {open_count}"
        )

    if not account_lines:
        # Fallback: no accounts discovered — show aggregate totals as before.
        trade_count, total_pnl = fetch_today_pnl()
        open_count = fetch_open_positions_count()
        label = get_strategy_label()
        account_lines.append(
            f"*{label}* trader: `{get_service_status(LIVE_SERVICE_NAME)}`\n"
            f"  📊 Trades today: {trade_count} | P&L: ${total_pnl:+.2f}\n"
            f"  📂 Open (DB): {open_count}"
        )

    accounts_block = "\n\n".join(account_lines)
    text = (
        "✅ *ICT Trading Bot Status*\n\n"
        f"🚦 Kill-switch: {halt_line}\n\n"
        f"{accounts_block}\n\n"
        f"🤖 Telegram bot: `{get_service_status('ict-telegram-bot')}`\n"
        f"🕐 {now}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_halt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        with open(HALT_FLAG_PATH, "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        # Also pause accounts via Coordinator so in-process risk guard fires.
        try:
            coord = get_coordinator()
            if coord is not None:
                coord.return_command("halt")
        except Exception as exc:
            logger.warning("cmd_halt: coordinator.return_command failed: %s", exc)
        await update.message.reply_text(
            "🛑 *Trader HALTED*\nFlag file created. No new orders will be placed.\n"
            "Use /resume to re-enable trading.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to create halt flag: {e}")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    if not os.path.exists(HALT_FLAG_PATH):
        await update.message.reply_text("ℹ️ Trader is not halted — no flag file found.")
        return
    try:
        os.remove(HALT_FLAG_PATH)
        # Also resume accounts via Coordinator.
        try:
            coord = get_coordinator()
            if coord is not None:
                coord.return_command("resume")
        except Exception as exc:
            logger.warning("cmd_resume: coordinator.return_command failed: %s", exc)
        await update.message.reply_text(
            "✅ *Trader RESUMED*\nHalt flag removed. Orders will resume on the next cycle.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Failed to remove halt flag: {e}")


def _render_account_balance(account: dict) -> str:
    """Dispatch a single account to the right balance formatter."""
    exchange = str((account or {}).get("exchange", "")).lower()
    if exchange == "bybit":
        return format_bybit_balance(account)
    if exchange == "binance":
        return format_binance_balance(account)
    label = get_strategy_label(account)
    return (
        f"💰 *{label} Balance*\n"
        f"Exchange=`{exchange or 'not set'}` — unsupported exchange."
    )


def _render_account_positions(account: dict) -> str:
    """Dispatch a single account to the right positions formatter."""
    exchange = str((account or {}).get("exchange", "")).lower()
    if exchange == "bybit":
        return format_bybit_positions(account)
    if exchange == "binance":
        return format_binance_positions(account)
    label = get_strategy_label(account)
    return (
        f"📊 *{label} Positions*\n"
        f"Exchange=`{exchange or 'not set'}` — unsupported exchange."
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    if not accounts:
        await update.message.reply_text(
            "⚠️ No accounts configured. Add `.env` (legacy) or `.env.<id>` files.",
            parse_mode="Markdown",
        )
        return
    blocks = []
    for acc in accounts:
        try:
            blocks.append(_render_account_balance(acc))
        except Exception as e:  # noqa: BLE001
            blocks.append(f"💰 *{acc.get('account_id', '?')} Balance*\n⚠️ {e}")
    await update.message.reply_text("\n\n".join(blocks), parse_mode="Markdown")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    if not accounts:
        await update.message.reply_text(
            "⚠️ No accounts configured. Add `.env` (legacy) or `.env.<id>` files.",
            parse_mode="Markdown",
        )
        return
    blocks = []
    for acc in accounts:
        try:
            blocks.append(_render_account_positions(acc))
        except Exception as e:  # noqa: BLE001
            blocks.append(f"📊 *{acc.get('account_id', '?')} Positions*\n⚠️ {e}")
    await update.message.reply_text("\n\n".join(blocks), parse_mode="Markdown")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": "BTCUSDT"}, timeout=10,
        )
        price = float(resp.json()["result"]["list"][0]["lastPrice"])
        await update.message.reply_text(f"📈 *BTC/USDT:* ${price:,.2f}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch price: {e}")


def _format_trade_row(row: dict) -> str:
    """Render one trade-journal row using the /last5 emoji template."""
    return (
        f"🔔 *Trade #{row['id']}*\n"
        f"🕒 {row['timestamp']}\n💱 {row['symbol']}\n📈 {row['direction']}\n"
        f"💰 Entry: {row['entry_price']}\n🛑 SL: {row['stop_loss']}\n"
        f"🎯 TP1: {row['take_profit_1']} | TP2: {row['take_profit_2']} | TP3: {row['take_profit_3']}\n"
        f"📦 Size: {row['position_size']}\n"
        f"🧠 {row['setup_type']} | {row['bias']} | {row['killzone']}\n"
        f"📝 {row['entry_reason']}\n🚪 {row['exit_reason']}\n"
        f"💵 PnL: {row['pnl']} ({row['pnl_percent']}%)\n"
        f"📌 {row['status']}\n📓 {row['notes']}\n"
        f"🧪 Backtest: {bool(row['is_backtest'])}\n🕒 {row['created_at']}"
    )


async def cmd_last5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    rows: list = []
    for acc in accounts:
        try:
            rows.extend(dl.recent_trades_for(acc, n=5) or [])
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(
                f"⚠️ {acc.get('account_id', '?')}: could not load trades: {e}"
            )
    if not rows:
        await update.message.reply_text("📭 No trades found in trade_journal.db.")
        return
    chart_candidates = [
        os.path.join(BASE_DIR, "ict_complete_chart.html"),
        os.path.join(BASE_DIR, "ict_enhanced_chart.html"),
        os.path.join(BASE_DIR, "swing_chart.html"),
    ]
    available_chart = next(
        (p for p in chart_candidates if os.path.exists(p)), None)
    for row in rows:
        try:
            await update.message.reply_text(
                _format_trade_row(row), parse_mode="Markdown")
            if available_chart:
                await update.message.reply_document(
                    document=open(available_chart, "rb"))
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(
                f"⚠️ Could not render trade: {e}")


# ---------------------------------------------------------------------------
# /signals — show recent pipeline signals from runtime_logs/signal_audit.jsonl
# ---------------------------------------------------------------------------

# S-012 PR E4 wires the audit log; this command surfaces it to the
# operator. Arguments:
#   /signals          → last 10 signals across all strategies
#   /signals 25       → last 25
#   /signals vwap     → last 10 for the vwap strategy
#   /signals turtle_soup 5
_SIG_AUDIT_CANDIDATES = [
    os.environ.get("SIGNAL_AUDIT_PATH", ""),
    os.path.join(REPO_ROOT, "runtime_logs", "signal_audit.jsonl"),
]
SIGNAL_AUDIT_PATH = next(
    (p for p in _SIG_AUDIT_CANDIDATES if p and os.path.exists(p)),
    os.path.join(REPO_ROOT, "runtime_logs", "signal_audit.jsonl"),
)
_SIGNAL_STATUS_EMOJI = {
    "submitted": "🟢",
    "dry_run":   "🟡",
    "skipped":   "⚪️",
    "halted":    "🛑",
    "failed_validation": "🔴",
    "failed_exchange":   "❌",
    "refused":   "🚫",
    "blocked":   "🚫",
}


def _read_audit_tail(path: str, limit: int) -> list[dict]:
    """Return the last ``limit`` JSON records from ``path`` (newest LAST)."""
    if not os.path.exists(path):
        return []
    try:
        # Pull the last 4× the limit lines from the end of the file so we
        # have headroom after filter/parse failures, but never the whole
        # file in memory.
        from collections import deque
        wanted = max(limit * 4, 50)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            tail = deque(fh, maxlen=wanted)
        out: list[dict] = []
        for line in tail:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("_read_audit_tail(%s): %s", path, exc)
        return []


def _format_signal_row(rec: dict) -> str:
    """Render one signal_audit.jsonl record for a Telegram block.

    Plain text only — pipeline statuses/reasons (``no_signal``,
    ``halt_flag_active``, ``failed_validation``) contain underscores that
    break Telegram's legacy Markdown italic parsing, so the original
    ``_..._``/``*...*`` wrappers caused a silent BadRequest on every reply.

    CP-2026-05-02: strategy is labelled (``strategy=…``) so the operator
    can see at a glance which strategy fired the signal — previously the
    bare token blended with symbol/side and was easy to miss.
    """
    ts = str(rec.get("logged_at_utc", ""))[:19].replace("T", " ")
    strategy = str(rec.get("strategy", "?"))
    symbol = str(rec.get("symbol", "?"))
    side = str(rec.get("side", "?"))
    qty = rec.get("qty")
    qty_s = f"{float(qty):.4f}" if isinstance(qty, (int, float)) else "?"
    status = str(rec.get("status", "?"))
    emoji = _SIGNAL_STATUS_EMOJI.get(status, "•")
    reason = str(rec.get("reason") or "")
    reason_s = f" — {reason[:60]}" if reason else ""
    return (
        f"{emoji} {ts} | strategy={strategy} | {symbol} {side} {qty_s} "
        f"→ {status}{reason_s}"
    )


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent signals from runtime_logs/signal_audit.jsonl."""
    if not is_authorised(update):
        return

    args = list(context.args or [])
    strategy_filter: str | None = None
    limit = 10
    for arg in args:
        if arg.isdigit():
            limit = max(1, min(int(arg), 100))
        else:
            strategy_filter = arg.strip().lower()

    records = _read_audit_tail(SIGNAL_AUDIT_PATH, limit * 5 if strategy_filter else limit)
    if strategy_filter:
        records = [r for r in records if str(r.get("strategy", "")).lower() == strategy_filter]
    records = records[-limit:]

    if not records:
        scope = f" for {strategy_filter}" if strategy_filter else ""
        await update.message.reply_text(
            f"📭 No signals logged yet{scope}.\n"
            f"Audit file: {SIGNAL_AUDIT_PATH}",
        )
        return

    header = (
        f"📡 Last {len(records)} signals"
        + (f" — {strategy_filter}" if strategy_filter else "")
    )
    body = "\n".join(_format_signal_row(r) for r in records)
    await update.message.reply_text(
        f"{header}\n{body}",
        disable_web_page_preview=True,
    )


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []
    if not accounts:
        try:
            log_text = get_last_logs(lines=20)
            label = get_strategy_label()
            await update.message.reply_text(
                f"📝 *{label} logs*\n```{log_text[-3500:]}```",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Could not read logs: {e}")
        return
    for acc in accounts:
        svc = acc.get("service") or LIVE_SERVICE_NAME
        label = get_strategy_label(acc)
        try:
            log_text = dl.recent_logs_for(svc, n=20)
            await update.message.reply_text(
                f"📝 *{label}* (`{svc}`)\n```{log_text[-3500:]}```",
                parse_mode="Markdown",
            )
        except Exception as e:  # noqa: BLE001
            await update.message.reply_text(f"⚠️ Could not read logs for `{svc}`: {e}")


async def cmd_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        accounts = dl.list_accounts() or []
    except Exception:
        accounts = []
    if not accounts:
        current = get_service_status(LIVE_SERVICE_NAME)
        action = "stop" if current == "active" else "start"
        result = toggle_service(LIVE_SERVICE_NAME, action)
        await update.message.reply_text(result, parse_mode="Markdown")
        return
    for acc in accounts:
        svc = acc.get("service") or LIVE_SERVICE_NAME
        current = get_service_status(svc)
        action = "stop" if current == "active" else "start"
        result = toggle_service(svc, action)
        await update.message.reply_text(result, parse_mode="Markdown")


# Display labels for per-strategy close buttons.
_CLOSE_BUTTON_LABELS = {
    "breakout_confirmation": "Breakout",
    "vwap": "VWAP",
    "ict": "ICT",
    "killzone": "KillZone",
}


async def _do_closeall_strategy(reply_fn, strategy_name: str) -> None:
    """Close positions for all Bybit accounts that run *strategy_name*."""
    try:
        accounts = dl.list_accounts() or []
        bybit_accounts = [a for a in accounts if (a.get("exchange") or "").lower() == "bybit"]
    except Exception as e:
        await reply_fn(f"⚠️ Could not list accounts: {e}")
        return
    if not bybit_accounts:
        await reply_fn("⚠️ No Bybit accounts configured.")
        return
    results = []
    for account in bybit_accounts:
        try:
            msg = dl.close_all_bybit_positions_for_strategy(account, strategy_name)
            if msg is not None:
                results.append(msg)
        except Exception as e:
            aid = account.get("account_id", "?")
            results.append(f"⚠️ Error ({aid}): {e}")
    if not results:
        await reply_fn(f"ℹ️ No accounts configured to run strategy '{strategy_name}'.")
        return
    await reply_fn("\n\n".join(results)[:4000], parse_mode="Markdown")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    # /closeall <strategy> → filter by strategy
    if context.args:
        strategy = context.args[0].strip().lower()
        await _do_closeall_strategy(update.message.reply_text, strategy)
        return
    # No args → inline keyboard for per-strategy selection + close-all
    strategies = dl.list_live_strategies() or list(_CLOSE_BUTTON_LABELS.keys())
    buttons = [
        InlineKeyboardButton(
            f"Close {_CLOSE_BUTTON_LABELS.get(s, s.title())}",
            callback_data=f"closeall:{s}",
        )
        for s in strategies
    ]
    buttons.append(InlineKeyboardButton("🚨 Close ALL", callback_data="closeall:all"))
    # Arrange in rows of 2
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard = InlineKeyboardMarkup(rows)
    try:
        accounts = dl.list_accounts() or []
        bybit_accounts = [a for a in accounts if (a.get("exchange") or "").lower() == "bybit"]
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not list accounts: {e}")
        return
    if not bybit_accounts:
        await update.message.reply_text("⚠️ No Bybit accounts configured.")
        return
    await update.message.reply_text(
        "Select strategy to close positions for:",
        reply_markup=keyboard,
    )


def _format_strategies_dashboard(rows: list) -> str:
    if not rows:
        return "📊 *Strategy Dashboard*\nNo strategies configured."
    lines = ["📊 *Strategy Dashboard*\n"]
    for r in rows:
        pnl = float(r.get("pnl", 0) or 0)
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        status = r.get("status", "active")
        icon = "✅" if status == "active" else "⏸"
        model_str = f" | 🧠 `{r['model']}`" if r.get("model") else ""
        lines.append(
            f"{icon} *{r['strategy']}*\n"
            f"  🔧 `{r.get('service', '?')}`{model_str}\n"
            f"  📡 {r.get('signals_today', 0)} signals | "
            f"💵 {pnl_str} | "
            f"📂 {r.get('open_pos', 0)} open"
        )
    return "\n\n".join(lines)


async def cmd_strategies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is not None:
            stats = coord.dashboard_stats()
            rows = stats.get("strategies") or []
        else:
            rows = dl.strategy_dashboard_data()
        text = _format_strategies_dashboard(rows)
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load strategy dashboard: {e}")


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the most recent alerts from all units (coordinator alerts queue)."""
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        alerts = coord.list_alerts(n=10)
        if not alerts:
            await update.message.reply_text("📭 No alerts in queue.")
            return
        lines = ["🔔 *Recent Alerts* (last 10)\n"]
        for a in reversed(alerts):
            level_icon = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(a.get("level", "info"), "ℹ️")
            ts = (a.get("ts") or "")[:19].replace("T", " ")
            lines.append(f"{level_icon} `{ts}` [{a.get('source', '?')}] {a.get('message', '')}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load alerts: {e}")


_SPRINT_RE = re.compile(r"\bS-\d{3}(?:\.\d+)?\b")
_CP_HEADER_RE = re.compile(r"^##\s+(CP-\d{4}-\d{2}-\d{2}-\d+)\b")


def _latest_sprint_from_checkpoint_log() -> tuple[str, str]:
    """Return ``(sprint_id, cp_id)`` parsed from the topmost CP entry of
    ``docs/claude/checkpoints/CHECKPOINT_LOG.md``. Falls back to
    ``("unknown", "unknown")`` on any read / parse error so a stale
    or missing log can never crash these commands."""
    log_path = os.path.join(REPO_ROOT, "docs", "claude", "checkpoints",
                            "CHECKPOINT_LOG.md")
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        return "unknown", "unknown"
    cp_id = "unknown"
    sprint_id = "unknown"
    in_entry = False
    for line in text.splitlines():
        m = _CP_HEADER_RE.match(line)
        if m and not in_entry:
            cp_id = m.group(1)
            in_entry = True
            continue
        if in_entry and line.startswith("- **Sprint:**"):
            ms = _SPRINT_RE.search(line)
            if ms:
                sprint_id = ms.group(0)
            break
        if in_entry and line.startswith("## "):
            break
    return sprint_id, cp_id


async def cmd_sprintlet_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual sprint milestone update. Usage: ``/sprintlet_status <note>``.

    The sprint id is parsed from the topmost CP entry of
    ``CHECKPOINT_LOG.md``, so the command is no longer hardcoded to
    a long-dead sprint number (fixed in S-016 H1 — see the audit doc).
    """
    if not is_authorised(update):
        return
    sprint_id, _ = _latest_sprint_from_checkpoint_log()
    note = " ".join(context.args) if context.args else "update"
    await update.message.reply_text(f"✅ {sprint_id}: {note}")


async def cmd_sprintlet_complete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manual sprint-complete signal. Usage:
    ``/sprintlet_complete [sprint]`` — defaults to the active sprint
    parsed from ``CHECKPOINT_LOG.md``."""
    if not is_authorised(update):
        return
    sprint_arg = context.args[0].strip().upper() if context.args else None
    if sprint_arg and _SPRINT_RE.fullmatch(sprint_arg):
        sprint_id = sprint_arg
        cp_id = "(see CHECKPOINT_LOG.md)"
    else:
        sprint_id, cp_id = _latest_sprint_from_checkpoint_log()
    await update.message.reply_text(
        f"🎉 {sprint_id} COMPLETE. Latest checkpoint: {cp_id}."
    )


# ── Pending-pings inbox (S-019) ────────────────────────────────────────────
#
# Any process on the VM (deploy script, smoke runner, trader, /vm session)
# can ping the operator without re-implementing the Telegram client by
# dropping a JSON file in `runtime_logs/pending_pings/`. The bot's job
# queue drains the directory every few seconds. This decouples ping
# emission from the git-sync timer — pings fire seconds after the file
# lands, not minutes.
#
# Schema: ``{"priority": "normal|high|urgent|low", "body": "..."}``.
# Atomic writes: writers create ``<id>.json.tmp`` then ``rename`` to
# ``<id>.json`` so the drainer never reads a half-written file.

PENDING_PINGS_DIR = os.path.join(REPO_ROOT, "runtime_logs", "pending_pings")
PING_DRAIN_INTERVAL_S = 5

_PRIORITY_ICONS = {
    "urgent": "🚨 URGENT",
    "high":   "🔔",
    "normal": "ℹ️",
    "low":    "·",
}


async def _drain_pending_pings(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue task — scan the inbox, send each, delete on success.

    Failures (Telegram 4xx, malformed JSON) move the offending file
    aside with a ``.broken`` suffix so the drainer doesn't loop on it.
    """
    try:
        os.makedirs(PENDING_PINGS_DIR, exist_ok=True)
        names = sorted(
            n for n in os.listdir(PENDING_PINGS_DIR)
            if n.endswith(".json") and not n.endswith(".tmp")
        )
    except OSError:
        return

    if not names:
        return

    chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        logger.warning("ping inbox has %d file(s) but TELEGRAM_CHAT_ID is unset",
                       len(names))
        return

    for name in names:
        path = os.path.join(PENDING_PINGS_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("ping inbox: malformed file %s — %s", name, exc)
            try:
                os.rename(path, path + ".broken")
            except OSError:
                pass
            continue

        priority = str(payload.get("priority", "normal")).lower()
        body = str(payload.get("body", "")).strip()
        if not body:
            try:
                os.unlink(path)
            except OSError:
                pass
            continue

        prefix = _PRIORITY_ICONS.get(priority, _PRIORITY_ICONS["normal"])
        text = f"{prefix} {body}"

        try:
            await context.bot.send_message(
                chat_id=chat_id, text=text,
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ping inbox: send failed for %s — %s", name, exc)
            continue   # leave file in place; retry next tick

        try:
            os.unlink(path)
        except OSError:
            pass


async def cmd_ping_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify the inbox-drain loop end-to-end.

    Drops a test ping in the inbox, waits one drain cycle, replies
    with success / diagnostic.
    """
    if not is_authorised(update):
        return
    note = " ".join(context.args) if context.args else "ping test"
    os.makedirs(PENDING_PINGS_DIR, exist_ok=True)
    test_id = f"test-{int(datetime.now(timezone.utc).timestamp())}"
    path = os.path.join(PENDING_PINGS_DIR, f"{test_id}.json")
    tmp = path + ".tmp"
    payload = {"priority": "normal", "body": f"ping_test from /ping_test: {note}"}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.rename(tmp, path)
    await update.message.reply_text(
        f"📨 Queued `{test_id}.json`. Should fire within "
        f"{PING_DRAIN_INTERVAL_S}s.",
        parse_mode="Markdown",
    )


async def cmd_checkpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the latest checkpoint ID from CHECKPOINT_LOG.md."""
    if not is_authorised(update):
        return
    log_path = os.path.join(REPO_ROOT, "docs", "claude", "checkpoints", "CHECKPOINT_LOG.md")
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            cp_lines = [ln.strip() for ln in fh if ln.strip().startswith("## CP-")]
        latest = cp_lines[0] if cp_lines else "No checkpoint found"
        await update.message.reply_text(f"Latest checkpoint: {latest}")
    except Exception as exc:
        await update.message.reply_text(f"⚠️ Could not read checkpoint log: {exc}")


# ── /health and /vmstats (S-016 H2) ──────────────────────────────────────────


_HEALTH_UNITS = (
    "ict-trader-live",
    "ict-telegram-bot",
    "ict-web-api",
    "ict-git-sync.timer",
)
_HEALTH_FILES: tuple[tuple[str, str], ...] = (
    # (display_label, repo_relative_path)
    ("runtime_status.json (last tick)", "runtime_logs/runtime_status.json"),
    ("signal_audit.jsonl (last signal)", "runtime_logs/signal_audit.jsonl"),
    ("trade_journal.db",                 "trade_journal.db"),
)


def _file_age(path: str) -> str:
    """Return a short freshness string for *path*, e.g. ``42s``, ``7m``,
    ``3h12m``. ``missing`` if the file isn't there. Used by /health."""
    if not os.path.exists(path):
        return "missing"
    try:
        mtime = os.path.getmtime(path)
        size = os.path.getsize(path)
    except OSError as exc:
        return f"stat-err: {exc.__class__.__name__}"
    age = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
    if age < 60:
        return f"{int(age)}s ({size}B)"
    if age < 3600:
        return f"{int(age / 60)}m ({size}B)"
    if age < 86400:
        h = int(age // 3600)
        m = int((age % 3600) // 60)
        return f"{h}h{m:02d}m ({size}B)"
    return f"{int(age / 86400)}d ({size}B)"


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Per-unit status snapshot — systemd units + key data files.

    Designed to fit in one Telegram message; no cross-process
    coordination, just file mtimes and ``systemctl is-active``.
    """
    if not is_authorised(update):
        return
    lines = ["🩺 *ICT Trading Bot — health*\n"]
    lines.append("*Services*")
    for unit in _HEALTH_UNITS:
        status = get_service_status(unit)
        icon = "🟢" if status == "active" else "🔴" if status == "failed" else "⚪️"
        lines.append(f"  {icon} `{unit}` — {status}")
    lines.append("\n*Data freshness*")
    for label, rel_path in _HEALTH_FILES:
        full = os.path.join(REPO_ROOT, rel_path)
        lines.append(f"  • {label}: `{_file_age(full)}`")
    lines.append(f"\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _read_loadavg() -> str:
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as fh:
            parts = fh.read().split()
        return " ".join(parts[:3]) if len(parts) >= 3 else "unknown"
    except OSError:
        return "unknown"


def _read_uptime_human() -> str:
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as fh:
            secs = float(fh.read().split()[0])
    except (OSError, ValueError):
        return "unknown"
    d, secs = divmod(int(secs), 86400)
    h, secs = divmod(secs, 3600)
    m, _ = divmod(secs, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def _read_meminfo_mb() -> tuple[int, int]:
    """Return (total_mb, available_mb). (0, 0) on read error."""
    total = avail = 0
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    avail = int(line.split()[1]) // 1024
                if total and avail:
                    break
    except (OSError, ValueError, IndexError):
        return 0, 0
    return total, avail


def _disk_usage_repo() -> tuple[int, int]:
    """Return (free_gb, total_gb) for the partition holding the repo."""
    try:
        import shutil
        total, _, free = shutil.disk_usage(REPO_ROOT)
        return free // (1024 ** 3), total // (1024 ** 3)
    except OSError:
        return 0, 0


async def cmd_vmstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VM-side resource snapshot — uptime, load, memory, disk."""
    if not is_authorised(update):
        return
    load = _read_loadavg()
    uptime = _read_uptime_human()
    mem_total, mem_avail = _read_meminfo_mb()
    mem_used_pct = (
        int(100 * (mem_total - mem_avail) / mem_total)
        if mem_total else 0
    )
    disk_free_gb, disk_total_gb = _disk_usage_repo()
    cpus = os.cpu_count() or 0
    lines = [
        "🖥️ *VM stats*\n",
        f"⏱️ Uptime: `{uptime}`",
        f"📈 Load (1/5/15 m): `{load}` on `{cpus}` CPU{'s' if cpus != 1 else ''}",
        (f"🧠 Memory: `{mem_total - mem_avail}` / `{mem_total}` MB "
         f"used (`{mem_used_pct}%`)" if mem_total else "🧠 Memory: unknown"),
        (f"💾 Disk (repo partition): `{disk_free_gb}` / `{disk_total_gb}` GB free"
         if disk_total_gb else "💾 Disk: unknown"),
        f"\n🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── VM-resident Claude runner (S-014.5) ──────────────────────────────────────
#
# /vm <prompt>        — Tier 1, read-only ops, no confirmation.
# /vm_write <prompt>  — Tier 2, mutating ops, requires Telegram YES/NO.
#
# Tier 3 actions are pre-screened in src.bot.vm_runner.screen_for_tier3 and
# refused here regardless of command. See docs/claude/vm-operator-mode.md.

# Pending /vm_write confirmations, keyed by chat id. Single-slot per chat —
# a second /vm_write while one is pending replaces the first (the first one
# is implicitly cancelled). Bot is single-operator anyway so this is fine.
_PENDING_VM_WRITE: dict[int, str] = {}

_VM_WRITE_BUTTONS = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Confirm", callback_data="vm_write_confirm"),
    InlineKeyboardButton("✖️ Cancel",  callback_data="vm_write_cancel"),
]])


async def _run_and_reply(
    update: Update, prompt: str, tier: int
) -> None:
    """Spawn the runner in a thread (it blocks on systemd-run --wait) and
    post the result back. Keeps the bot's event loop responsive."""
    chat = update.effective_chat
    progress = await update.message.reply_text(
        f"🤖 VM runner spawned (tier {tier}). Waiting for transcript…"
    )
    result: RunnerResult = await asyncio.to_thread(handle_vm_command, prompt, tier)
    icon = "✅" if result.ok else "⚠️"
    await progress.edit_text(f"{icon} {result.telegram_text()}")


async def cmd_vm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tier 1 — read-only VM diagnostics via Claude Code."""
    if not is_authorised(update):
        return
    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text(
            "Usage: /vm <prompt>\n"
            "Example: /vm what is the trader uptime and last error?\n"
            f"Max {MAX_PROMPT_CHARS} chars. Tier 1 only — read-only ops."
        )
        return
    await _run_and_reply(update, prompt, tier=1)


async def cmd_vm_write(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tier 2 — mutating ops. Stages the prompt; user confirms via inline button."""
    if not is_authorised(update):
        return
    prompt = " ".join(context.args or []).strip()
    if not prompt:
        await update.message.reply_text(
            "Usage: /vm_write <prompt>\n"
            "Tier 2 — service restarts, file edits, branch push, PR open. "
            "You'll be asked to confirm before the runner spawns."
        )
        return
    chat_id = update.effective_chat.id
    _PENDING_VM_WRITE[chat_id] = prompt
    preview = prompt if len(prompt) <= 600 else prompt[:600] + "…"
    await update.message.reply_text(
        "⚠️ *Tier 2 (write) confirmation*\n\n"
        f"```\n{preview}\n```\n\n"
        "Confirm to spawn the runner with write permissions, or cancel.",
        parse_mode="Markdown",
        reply_markup=_VM_WRITE_BUTTONS,
    )


async def cmd_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply with a tap-to-open button for the secure web dashboard.

    Reads ``WEBAPP_URL`` from the environment. Unset/empty → clean
    "not configured yet" message. The dashboard requires the operator
    to log in (S-013 M3) before any data is shown.
    """
    if not is_authorised(update):
        return
    url = (os.environ.get("WEBAPP_URL") or "").strip()
    if not url:
        await update.message.reply_text(
            "🌐 Web dashboard not configured yet.\n"
            "Set WEBAPP_URL on the VM once the dashboard is reachable."
        )
        return
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔐 Open dashboard", url=url)]]
    )
    await update.message.reply_text(
        "🌐 Web dashboard — log in with your allowlisted email.",
        reply_markup=keyboard,
    )


async def cmd_download_journal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    if not os.path.exists(DB_PATH):
        await update.message.reply_text("⚠️ trade_journal.db not found.")
        return
    try:
        with open(DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f, filename="trade_journal.db",
                caption="📥 Latest trade_journal.db",
            )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not send journal: {e}")


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BACKTEST_TASK, BACKTEST_STATUS
    if not is_authorised(update):
        return
    if BACKTEST_STATUS["state"] == "running":
        await update.message.reply_text("⏳ Backtest already running. Use /latest_backtest to check.")
        return
    BACKTEST_TASK = asyncio.create_task(run_backtest_in_background(context.application))
    await update.message.reply_text("🚀 Backtest started. Use /latest_backtest to see status and results.")


async def cmd_latest_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    state = BACKTEST_STATUS["state"]
    if state == "running":
        await update.message.reply_text(
            f"⏳ *Backtest RUNNING*\nStarted: {BACKTEST_STATUS['started_at']}", parse_mode="Markdown"
        )
    elif state == "failed":
        await update.message.reply_text(
            f"⚠️ *Backtest FAILED*\nStarted: {BACKTEST_STATUS['started_at']}\n"
            f"Finished: {BACKTEST_STATUS['finished_at']}\nCode: {BACKTEST_STATUS['last_returncode']}\n"
            f"Error: {BACKTEST_STATUS['last_error']}",
            parse_mode="Markdown",
        )
    elif state == "completed":
        rows = dl.latest_backtests_per_model()
        latest = rows[0] if rows else None
        if latest:
            await update.message.reply_text(format_backtest_summary(latest), parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"✅ *Backtest COMPLETED*\nFinished: {BACKTEST_STATUS['finished_at']}", parse_mode="Markdown"
            )
    else:
        rows = dl.latest_backtests_per_model()
        latest = rows[0] if rows else None
        if latest:
            await update.message.reply_text(format_backtest_summary(latest), parse_mode="Markdown")
        else:
            await update.message.reply_text("ℹ️ No backtest running and no saved result found.")


# ── Inline button callback handler ───────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_authorised(update):
        await query.edit_message_text("⛔ Unauthorised.")
        return

    raw = query.data or ""
    if raw == "vm_write_confirm":
        chat_id = update.effective_chat.id
        prompt = _PENDING_VM_WRITE.pop(chat_id, "")
        if not prompt:
            await query.edit_message_text("⏰ Confirmation expired or already actioned.")
            return
        await query.edit_message_text("🤖 Tier 2 confirmed — runner spawned. Waiting for transcript…")
        result: RunnerResult = await asyncio.to_thread(handle_vm_command, prompt, 2)
        icon = "✅" if result.ok else "⚠️"
        await query.message.reply_text(f"{icon} {result.telegram_text()}")
        return
    if raw == "vm_write_cancel":
        chat_id = update.effective_chat.id
        _PENDING_VM_WRITE.pop(chat_id, None)
        await query.edit_message_text("✖️ Cancelled.")
        return

    parts = raw.split(":", 1)
    if not parts or not parts[0]:
        return
    action = parts[0]

    if action == "log":
        try:
            accounts = dl.list_accounts() or []
        except Exception:
            accounts = []
        if not accounts:
            try:
                log_text = get_last_logs(lines=20)
                label = get_strategy_label()
                await query.edit_message_text(
                    f"📝 *{label} logs*\n```{log_text[-3500:]}```",
                    parse_mode="Markdown",
                )
            except Exception as e:
                await query.edit_message_text(f"⚠️ Could not read logs: {e}")
        else:
            blocks = []
            for acc in accounts:
                svc = acc.get("service") or LIVE_SERVICE_NAME
                label = get_strategy_label(acc)
                log_text = dl.recent_logs_for(svc, n=10)
                blocks.append(f"📝 *{label}* (`{svc}`)\n```{log_text[-1200:]}```")
            try:
                await query.edit_message_text(
                    "\n\n".join(blocks)[:4000], parse_mode="Markdown"
                )
            except Exception as e:
                await query.edit_message_text(f"⚠️ Could not read logs: {e}")

    elif action == "toggle":
        try:
            accounts = dl.list_accounts() or []
        except Exception:
            accounts = []
        if not accounts:
            current = get_service_status(LIVE_SERVICE_NAME)
            act = "stop" if current == "active" else "start"
            result = toggle_service(LIVE_SERVICE_NAME, act)
            await query.edit_message_text(result, parse_mode="Markdown")
        else:
            results = []
            for acc in accounts:
                svc = acc.get("service") or LIVE_SERVICE_NAME
                current = get_service_status(svc)
                act = "stop" if current == "active" else "start"
                results.append(toggle_service(svc, act))
            await query.edit_message_text(
                "\n\n".join(results)[:4000], parse_mode="Markdown"
            )

    elif action == "closeall":
        payload = parts[1] if len(parts) > 1 else "all"
        if payload != "all":
            # Per-strategy close via inline button
            await _do_closeall_strategy(query.edit_message_text, payload)
        else:
            # Close ALL positions across all Bybit accounts
            try:
                accounts = dl.list_accounts() or []
                bybit_accounts = [
                    a for a in accounts if (a.get("exchange") or "").lower() == "bybit"
                ]
            except Exception as e:
                await query.edit_message_text(f"⚠️ Could not list accounts: {e}")
                return
            if not bybit_accounts:
                await query.edit_message_text("⚠️ No Bybit accounts configured.")
                return
            await query.edit_message_text(
                f"🚨 Closing positions across {len(bybit_accounts)} Bybit account(s)…"
            )
            results = []
            for account in bybit_accounts:
                try:
                    results.append(close_all_bybit_positions(account))
                except Exception as e:
                    aid = account.get("account_id", "?")
                    results.append(f"⚠️ Error ({aid}): {e}")
            await query.edit_message_text(
                "\n\n".join(results)[:4000], parse_mode="Markdown"
            )


async def cmd_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List accounts with dry/live mode, or toggle: /accounts dry|live <name>"""
    if not is_authorised(update):
        return
    coord = get_coordinator()
    if coord is None:
        await update.message.reply_text("⚠️ Coordinator unavailable.")
        return

    # /accounts dry bybit_1  or  /accounts live bybit_1
    if len(context.args) == 2:
        mode = context.args[0].strip().lower()
        acc_name = context.args[1].strip()
        if mode not in ("dry", "live"):
            await update.message.reply_text(
                "⚠️ Usage: `/accounts dry|live <account_name>`",
                parse_mode="Markdown",
            )
            return
        try:
            result = coord.set_account_dry_run(acc_name, mode == "dry")
            icon = "🧪" if result["dry_run"] else "🔴"
            await update.message.reply_text(
                f"{icon} `{acc_name}` → **{result['mode']} mode**",
                parse_mode="Markdown",
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Could not toggle account: {e}")
        return

    # /accounts → list all with dry/live status
    try:
        statuses = coord.accounts_status()
        if not statuses:
            await update.message.reply_text("ℹ️ No accounts found in accounts.yaml.")
            return
        lines = ["📋 *Accounts* (dry/live + risk)\n"]
        for s in statuses:
            dry = s.get("dry_run", True)
            mode_icon = "🧪 dry" if dry else "🔴 live"
            halted_icon = " 🛑HALTED" if s.get("halted") else ""
            pnl = float(s.get("daily_pnl", 0))
            limit = float(s.get("max_daily_loss_usd", 0))
            lines.append(
                f"{mode_icon}{halted_icon} — *{s['name']}* (`{s.get('exchange', '?')}`)\n"
                f"  💵 PnL ${pnl:+.2f} / ${limit:.0f} | Type: {s.get('account_type', '?')}"
            )
        lines.append("\nUse `/accounts dry|live <name>` to toggle.")
        await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load accounts: {e}")


async def cmd_reload_strats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reload strategy config from strategies.yaml and confirm via Coordinator."""
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        result = coord.reload_strategy_config()
        if result.get("reloaded"):
            names = ", ".join(f"`{s}`" for s in result.get("strategies", []))
            await update.message.reply_text(
                f"✅ *Strategy config reloaded*\n"
                f"{result['strategy_count']} strategies: {names}",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"⚠️ Reload failed: {result.get('error', 'unknown error')}"
            )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not reload strategy config: {e}")


async def cmd_backtest_ui(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tell the user how to launch the Streamlit backtesting dashboard."""
    if not is_authorised(update):
        return
    await update.message.reply_text(
        "📈 *Backtesting UI*\n\n"
        "Run locally:\n"
        "```\nstreamlit run src/web/backtest_ui.py\n```\n\n"
        "Data sources (in order):\n"
        "1. `BACKTEST_CSV` env var\n"
        "2. `data/backtests.csv`\n"
        "3. `data/backtest_candles.csv`\n"
        "4. Mock data (always available)\n\n"
        "Filters: strategy, symbol, date range.",
        parse_mode="Markdown",
    )


async def cmd_accounts_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show per-account risk state + live API balance via Coordinator.

    The live balance line is what proves the API is integrated for each
    account. Anything other than a USD figure means the bot can't reach
    that account's exchange (missing creds, network, etc.).
    """
    if not is_authorised(update):
        return
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        statuses = coord.accounts_status()
        if not statuses:
            await update.message.reply_text("ℹ️ No accounts found in accounts.yaml.")
            return

        # Telegram's legacy "Markdown" parse mode treats `_` as italic
        # markers and silently strips them — env-var names like
        # BYBIT_API_KEY_1 in error strings rendered as BYBITAPIKEY1.
        # `\_` escapes don't work in legacy Markdown either (they only
        # work in MarkdownV2). HTML parse mode has the simplest reliable
        # escaping: just &amp; &lt; &gt; for the three special chars.
        def _h(s: object) -> str:
            text = str(s) if s is not None else ""
            return (
                text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
            )

        lines = ["📋 <b>Accounts Status</b> (risk + live API)\n"]
        for s in statuses:
            halted_icon = "🔴" if s.get("halted") else "🟢"
            pnl = float(s.get("daily_pnl", 0))
            limit = float(s.get("max_daily_loss_usd", 0))
            pos_size = float(s.get("max_pos_size_usd", 0))
            open_pos = s.get("open_positions", 0)
            bal = s.get("live_balance_usdt")
            bal_err = s.get("live_balance_error")
            strategies = s.get("strategies") or []
            strat_line = (
                f"  🎯 Strategy: {_h(', '.join(strategies))}\n"
                if strategies else
                "  🎯 Strategy: <i>(none assigned)</i>\n"
            )
            # BUG-033: show the last 4 chars of the resolved API key so the
            # operator can spot two accounts wired to the same wallet at a
            # glance (the symptom that opened this issue).
            key_fp = s.get("api_key_fingerprint") or "—"
            fp_line = f"  🔑 Key: …{_h(key_fp)}\n"
            if bal_err:
                api_line = f"  🔌 API: ❌ {_h(bal_err)}"
            elif bal is not None:
                api_line = f"  🔌 API: ✅ Balance ${float(bal):,.2f} USDT"
            else:
                api_line = "  🔌 API: ⚠️ no balance returned"
            lines.append(
                f"{halted_icon} <b>{_h(s['name'])}</b> "
                f"(<code>{_h(s.get('exchange', '?'))}</code> / {_h(s.get('account_type', '?'))})\n"
                f"{strat_line}"
                f"{fp_line}"
                f"{api_line}\n"
                f"  💵 Daily PnL: ${pnl:+.2f} / limit ${limit:.0f}\n"
                f"  📦 Max pos: ${pos_size:.0f} | Open: {open_pos}"
            )
        await update.message.reply_text("\n\n".join(lines), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load accounts status: {e}")


def _smoke_test_client_factory(account_cfg: dict):
    """Resolve a per-account exchange client for the smoke test.

    Dispatches on ``account_cfg['exchange']`` so multi-account smoke
    runs use each account's own keys (passing one client to every
    account would mis-route orders into the wrong wallet).
    """
    exchange = str((account_cfg or {}).get("exchange", "")).lower()
    if exchange == "bybit":
        return dl.bybit_client_for(account_cfg)
    if exchange == "binance":
        try:
            return dl.binance_conn_for(account_cfg)
        except AttributeError:
            return None
    return None


async def cmd_smoke_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run a live-plumbing smoke trade: strategies → coordinator → accounts → exchange.

    Usage:
        /smoke_test            — every account in accounts.yaml
        /smoke_test <account>  — a single account (e.g. /smoke_test bybit_2)

    The smoke is **always LIVE** — there is no dry-run option. A
    ``smoke_test`` OrderPackage tagged ``meta.is_test=True`` is
    shipped through ``account_execute()`` with a 0.0001 BTC qty (below
    Bybit's min-lot), the risk gate is bypassed by design, Bybit's
    "qty invalid" rejection is captured as the success signal, and a
    row is written to ``trade_journal.db`` with
    ``strategy_name='smoke_test'``. The qty is the safety cap — it's
    intentionally too small to ever fill, so contacting the exchange
    is safe and proves the API integration is hot end-to-end.
    """
    if not is_authorised(update):
        return
    coord = get_coordinator()
    if coord is None:
        await update.message.reply_text("⚠️ Coordinator unavailable.")
        return

    args = list(context.args or [])
    account_id: str | None = None
    for arg in args:
        a = arg.strip().lower()
        if a in {"all", "*"}:
            account_id = None
        elif account_id is None:
            account_id = arg.strip()

    await update.message.reply_text(
        ("🧪 Running smoke test (LIVE)"
         + (f" on `{account_id}`" if account_id else " on all accounts")
         + "…"),
        parse_mode="Markdown",
    )

    try:
        result = await asyncio.to_thread(
            coord.smoke_test_run,
            account_id,
            exchange_client_factory=_smoke_test_client_factory,
        )
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ smoke_test failed: {exc}")
        return

    smoke_id = result.get("smoke_id", "?")
    pkg = result.get("package", {})
    lines = [
        f"🧪 *Smoke test* `{smoke_id}`",
        f"Symbol: `{pkg.get('symbol', '?')}` | Dir: `{pkg.get('direction', '?')}`"
        f" | Qty: `{pkg.get('qty', '?')}`",
        "",
    ]
    if not result.get("results"):
        lines.append("⚠️ No accounts evaluated. " + str(result.get("error") or ""))
    for r in result.get("results", []):
        status = r.get("status", "?")
        icon = {
            "rejected_too_small": "✅",   # expected/success path
            "submitted":          "⚠️",   # unexpected acceptance — flatten manually
            "error":              "❌",
        }.get(status, "❌")
        reason = (r.get("reason") or "")[:160]
        logged = "📝" if r.get("logged") else "⚠️ not-logged"
        lines.append(
            f"{icon} `{r.get('account_id', '?')}` ({r.get('exchange', '?')})"
            f" — *{status}* {logged}\n  ↳ {reason}"
        )

    if result.get("ok"):
        lines.append("\n*Test successful*: order reached the exchange and was "
                     "rejected (below min-lot) — API integration is live.")
    else:
        lines.append("\n⚠️ *Test failed*: see per-account reasons above. "
                     "If reason mentions credentials, the bot's environment "
                     "is missing the per-account API key/secret env vars.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_risk_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check risk state for a specific account. Usage: /risk_check <account_name>"""
    if not is_authorised(update):
        return
    account_name = (context.args[0].strip() if context.args else "").lower()
    try:
        coord = get_coordinator()
        if coord is None:
            await update.message.reply_text("⚠️ Coordinator unavailable.")
            return
        statuses = coord.accounts_status()
        if not statuses:
            await update.message.reply_text("ℹ️ No accounts found in accounts.yaml.")
            return
        if not account_name:
            names = ", ".join(f"`{s['name']}`" for s in statuses)
            await update.message.reply_text(
                f"ℹ️ Specify an account name. Available: {names}",
                parse_mode="Markdown",
            )
            return
        match = next((s for s in statuses if s["name"].lower() == account_name), None)
        if match is None:
            names = ", ".join(f"`{s['name']}`" for s in statuses)
            await update.message.reply_text(
                f"⚠️ Account `{account_name}` not found.\nAvailable: {names}",
                parse_mode="Markdown",
            )
            return
        halted_icon = "🔴 HALTED" if match.get("halted") else "🟢 OK"
        pnl = float(match.get("daily_pnl", 0))
        limit = float(match.get("max_daily_loss_usd", 0))
        remaining = float(match.get("daily_loss_remaining", limit + pnl))
        pos_size = float(match.get("max_pos_size_usd", 0))
        dd_pct = float(match.get("max_dd_pct", 0)) * 100
        open_pos = match.get("open_positions", 0)
        text = (
            f"🔍 *Risk Check: {match['name']}*\n\n"
            f"Status: {halted_icon}\n"
            f"Exchange: `{match.get('exchange', '?')}` | Type: `{match.get('account_type', '?')}`\n\n"
            f"💵 Daily PnL: ${pnl:+.2f}\n"
            f"💰 Daily loss limit: ${limit:.0f}\n"
            f"🔋 Remaining budget: ${remaining:.2f}\n"
            f"📦 Max position size: ${pos_size:.0f}\n"
            f"📉 Max drawdown: {dd_pct:.1f}%\n"
            f"📂 Open positions: {open_pos}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not check risk for '{account_name}': {e}")


async def cmd_hourly(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Build + send the hourly summary on demand.

    BUG-032: the in-process scheduler in `src/main.py` is the only path
    that emits the hourly summary; if the trader process is in a tick-
    loop crash spiral, a stuck `summary_markers.json`, or
    `send_via_alert_manager` is failing, the operator sees nothing.
    `/hourly` bypasses the dedup marker and reports the build/send
    result back over the same Telegram channel so the failure mode
    becomes visible.

    Optional first arg ``replay`` reuses the marker (same as the
    scheduled path); otherwise the dedup is bypassed.
    """
    if not is_authorised(update):
        return

    bypass_dedup = True
    args = list(context.args or [])
    if args and args[0].strip().lower() in {"replay", "scheduled"}:
        bypass_dedup = False

    try:
        from datetime import datetime, timezone
        from src.runtime.hourly_report import build_hourly_report
        from src.runtime.outcomes import send_scheduled

        now = datetime.now(timezone.utc)
        if not bypass_dedup:
            from src.utils.signal_audit_logger import should_send_summary
            if not should_send_summary(now):
                await update.message.reply_text(
                    "ℹ️ /hourly replay: marker already present for this hour. "
                    "Use plain /hourly to force-send."
                )
                return

        msg = build_hourly_report(now_utc=now, tick_interval_s=900)
        send_scheduled(msg)

        await update.message.reply_text(
            f"✅ Hourly report dispatched ({len(msg)} chars). "
            f"If you don't see it shortly, check "
            f"`runtime_logs/pending_pings.jsonl` on the VM "
            f"(send_via_alert_manager failure path).",
            parse_mode="Markdown",
        )
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(
            f"⚠️ /hourly failed: {type(exc).__name__}: {exc}"
        )


async def cmd_set_all_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force every account out of dry-run mode.

    BUG-031 follow-up: when the operator wants to make sure every
    account is firing real orders, this is the single button that
    flips them all. Iterates accounts.yaml via the Coordinator and
    calls ``set_account_dry_run(name, False)`` on each.

    Reports a summary back over Telegram (account count, any failures).
    The process-level ``ALLOW_LIVE_TRADING`` env var is unaffected
    here — that lives in ``.env.live`` and changing it requires a
    trader restart. The per-account ``dry_run`` toggle is in-memory
    and applies to the next ``load_accounts()`` call (no restart).
    """
    if not is_authorised(update):
        return
    coord = get_coordinator()
    if coord is None:
        await update.message.reply_text("⚠️ Coordinator unavailable.")
        return
    try:
        statuses = coord.accounts_status()
    except Exception as exc:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Could not list accounts: {exc}")
        return

    flipped, errors = [], []
    for s in statuses:
        name = s.get("name")
        if not name:
            continue
        try:
            coord.set_account_dry_run(name, False)
            flipped.append(name)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")

    lines = ["🔴 <b>All accounts → LIVE</b>"]
    if flipped:
        lines.append(f"flipped: {', '.join(flipped)}")
    if errors:
        lines.append("errors:\n  - " + "\n  - ".join(errors))
    if not flipped and not errors:
        lines.append("(no accounts found)")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # S-019 — pending-pings inbox drain. Job queue runs the coroutine
    # every PING_DRAIN_INTERVAL_S seconds. Any process can drop a JSON
    # file into runtime_logs/pending_pings/ and it'll be sent within
    # one tick. See _drain_pending_pings docstring above.
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            _drain_pending_pings,
            interval=PING_DRAIN_INTERVAL_S,
            first=2,   # 2 s grace at startup so post_init lands first
            name="drain_pending_pings",
        )
    else:
        logger.warning(
            "JobQueue unavailable — pending-pings inbox drain disabled. "
            "Install python-telegram-bot[job-queue] to enable.",
        )

    async def post_init(app):
        label = get_strategy_label()
        commands = [
            BotCommand("start", "Show help"),
            BotCommand("help", "Show help"),
            BotCommand("status", "Kill-switch state, P&L summary, service status"),
            BotCommand("halt", "Stop order placement immediately"),
            BotCommand("resume", "Re-enable order placement"),
            BotCommand("closeall", f"Close all {label} positions"),
            BotCommand("toggle", f"Start/stop {label} trader"),
            BotCommand("accounts", "List accounts or toggle dry/live: /accounts dry|live <name>"),
            BotCommand("accounts_status", "Per-account risk state (daily PnL, halted)"),
            BotCommand("set_all_live", "Flip every account out of dry-run into live mode"),
            BotCommand("hourly", "Send the hourly summary on demand (bypasses dedup)"),
            BotCommand("risk_check", "Risk details for one account: /risk_check <name>"),
            BotCommand("smoke_test", "Live-plumbing smoke (always LIVE): /smoke_test [account]"),
            BotCommand("strategies", "Per-strategy signals, PnL and positions"),
            BotCommand("reload_strats", "Reload strategy config from strategies.yaml"),
            BotCommand("balance", "Account balance"),
            BotCommand("trades", "Open positions"),
            BotCommand("last5", "Last 5 journal entries"),
            BotCommand("signals", "Recent pipeline signals: /signals [N] [strategy]"),
            BotCommand("alerts", "Recent unit alerts (coordinator queue)"),
            BotCommand("log", f"Show {label} trader logs"),
            BotCommand("download_journal", "Download trade journal DB"),
            BotCommand("price", "Current BTC price"),
            BotCommand("backtest", "Run backtest"),
            BotCommand("latest_backtest", "Latest backtest result"),
            BotCommand("backtest_ui", "How to launch the Streamlit backtesting dashboard"),
            BotCommand("webapp", "Open the secure web dashboard"),
            BotCommand("vm", "Tier 1 read-only Claude on the VM"),
            BotCommand("vm_write", "Tier 2 mutating Claude on the VM (asks to confirm)"),
            BotCommand("checkpoint", "Show latest checkpoint from CHECKPOINT_LOG.md"),
            BotCommand("health", "Per-unit status + data-file freshness"),
            BotCommand("vmstats", "VM resource snapshot (uptime, load, mem, disk)"),
            BotCommand("ping_test", "Verify the pending-pings inbox drain loop"),
            BotCommand("sprintlet_status", "Manual sprint milestone update: /sprintlet_status [note]"),
            BotCommand("sprintlet_complete", "Manual sprint-complete signal: /sprintlet_complete [sprint]"),
        ]
        await app.bot.set_my_commands(commands)

    application.post_init = post_init
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("set_keys", cmd_set_keys))
    application.add_handler(CommandHandler("halt", cmd_halt))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("trades", cmd_trades))
    application.add_handler(CommandHandler("closeall", cmd_closeall))
    application.add_handler(CommandHandler("strategies", cmd_strategies))
    application.add_handler(CommandHandler("last5", cmd_last5))
    application.add_handler(CommandHandler("signals", cmd_signals))
    application.add_handler(CommandHandler("backtest", cmd_backtest))
    application.add_handler(CommandHandler("latest_backtest", cmd_latest_backtest))
    application.add_handler(CommandHandler("log", cmd_log))
    application.add_handler(CommandHandler("toggle", cmd_toggle))
    application.add_handler(CommandHandler("download_journal", cmd_download_journal))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("alerts", cmd_alerts))
    application.add_handler(CommandHandler("reload_strats", cmd_reload_strats))
    application.add_handler(CommandHandler("backtest_ui", cmd_backtest_ui))
    application.add_handler(CommandHandler("accounts", cmd_accounts))
    application.add_handler(CommandHandler("accounts_status", cmd_accounts_status))
    application.add_handler(CommandHandler("set_all_live", cmd_set_all_live))
    application.add_handler(CommandHandler("hourly", cmd_hourly))
    application.add_handler(CommandHandler("risk_check", cmd_risk_check))
    application.add_handler(CommandHandler("smoke_test", cmd_smoke_test))
    application.add_handler(CommandHandler("sprintlet_status", cmd_sprintlet_status))
    application.add_handler(CommandHandler("sprintlet_complete", cmd_sprintlet_complete))
    application.add_handler(CommandHandler("checkpoint", cmd_checkpoint))
    application.add_handler(CommandHandler("health", cmd_health))
    application.add_handler(CommandHandler("vmstats", cmd_vmstats))
    application.add_handler(CommandHandler("ping_test", cmd_ping_test))
    application.add_handler(CommandHandler("webapp", cmd_webapp))
    application.add_handler(CommandHandler("vm", cmd_vm))
    application.add_handler(CommandHandler("vm_write", cmd_vm_write))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling()


if __name__ == "__main__":
    main()
