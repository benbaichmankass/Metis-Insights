from src.runtime.signal_notifications import get_last_signals, format_signals
import os
import logging
import sqlite3
import asyncio
import sys
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv, dotenv_values
from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import requests

# Sprint S-001 PR-C: route data access through the data_loaders facade so the
# bot has one stable interface for journalctl, signals/backtests, and exchange
# queries. Inline helpers (get_last_logs, fetch_latest_backtest_result, etc.)
# remain for now to avoid breaking other call sites; later PRs prune them.
from src.bot import data_loaders as dl

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

# The bot operates on a single live trader, configured via the .env at the
# repo root.
LIVE_ENV_PATH = os.path.join(REPO_ROOT, ".env")

# Single systemd service for the trader. Used to build journalctl/systemctl
# commands. Kept as a constant so any future rename happens in one place.
LIVE_SERVICE_NAME = "ict-trader-live"

BACKTESTER_PATH = os.path.join(os.path.dirname(BASE_DIR), "backtest", "run_backtest.py")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

HALT_FLAG_PATH = "/tmp/trader_halt.flag"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

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


def fetch_today_pnl() -> tuple:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
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


def fetch_open_positions_count() -> int:
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM trades WHERE status = 'open' AND is_backtest = 0"
        )
        row = cur.fetchone()
        conn.close()
        return row[0] or 0
    except Exception:
        return 0


def load_account_env() -> dict:
    """Load environment variables from the live trader .env file.

    There is only one trader (live). Returns an empty dict when the file is
    missing so callers can render help text without crashing on a fresh box.
    """
    if not os.path.exists(LIVE_ENV_PATH):
        return {}
    values = dotenv_values(LIVE_ENV_PATH)
    return {k: v for k, v in values.items() if v is not None}


def get_bybit_client_from_env(env_vars: dict):
    from pybit.unified_trading import HTTP
    return HTTP(
        testnet=False,
        api_key=env_vars.get("BYBIT_API_KEY"),
        api_secret=env_vars.get("BYBIT_API_SECRET"),
    )


_STRATEGY_DISPLAY = {
    "killzone": "ICT",
    "ict": "ICT",
    "vwap": "VWAP",
    "breakout": "Breakout",
    "multiplexed": "Multi",
}

# Default label when STRATEGY env var is missing or unrecognised. The bot is
# live-trading only; this fallback should rarely be visible.
_DEFAULT_STRATEGY_LABEL = "Strategy"


def get_strategy_label(env_vars: dict | None = None) -> str:
    """Return the display name for the active strategy.

    Reads ``STRATEGY`` (or legacy ``STRATEGY_NAME``) from the supplied env vars
    or, if none are supplied, from the live ``.env`` on disk. Falls back to
    ``_DEFAULT_STRATEGY_LABEL`` when STRATEGY is unset or unknown. Defensive
    against missing/malformed env files because this is called at
    ``post_init`` time and must never crash the bot.
    """
    try:
        if env_vars is None:
            env_vars = load_account_env()
        raw = str(env_vars.get("STRATEGY", env_vars.get("STRATEGY_NAME", ""))).strip().lower()
        return _STRATEGY_DISPLAY.get(raw, _DEFAULT_STRATEGY_LABEL)
    except Exception:
        return _DEFAULT_STRATEGY_LABEL


def format_target_options(separator: str = "|") -> str:
    """Return the strategy label shown in slash-command help text.

    Returns the single active strategy's display name. ``separator`` is kept
    for API compatibility but is unused with one label.
    """
    return get_strategy_label()


def fetch_last_5_trades():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, timestamp, symbol, direction, entry_price, exit_price,
               stop_loss, take_profit_1, take_profit_2, take_profit_3,
               position_size, setup_type, killzone, bias, entry_reason,
               exit_reason, pnl, pnl_percent, status, notes,
               is_backtest, created_at
        FROM trades
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 5
        """
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_latest_backtest_result():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, run_date, strategy_version, start_date, end_date,
               total_trades, winning_trades, losing_trades, win_rate,
               profit_factor, expectancy, max_drawdown, max_drawdown_pct,
               sharpe_ratio, total_pnl, total_pnl_pct, avg_win, avg_loss,
               largest_win, largest_loss, created_at
        FROM backtest_results
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    conn.close()
    return row


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


def toggle_service(service_name: str, action: str) -> str:
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


def format_bybit_balance(account: dict) -> str:
    """Render the per-coin Bybit balance block for one account.
    Data is sourced via ``dl.account_balance``; this function only formats."""
    label = get_strategy_label(_account_env(account))
    payload = dl.account_balance(account)
    if payload is None:
        return f"💰 *{label} Balance*\n⚠️ Bybit error: balance unavailable."
    raw = (payload or {}).get("raw") or {}
    result_list = (raw.get("result") or {}).get("list") or []
    if not result_list:
        return f"💰 *{label} Balance*\nNo balance data returned from Bybit."
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
    return f"💰 *{label} Balance*\n{text}"


def format_bybit_positions(account: dict) -> str:
    """Render the open-positions block for one Bybit account using
    ``dl.account_open_positions`` output."""
    label = get_strategy_label(_account_env(account))
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


def close_all_bybit_positions(env_vars: dict) -> str:
    label = get_strategy_label(env_vars)
    client = get_bybit_client_from_env(env_vars)
    resp = client.get_positions(category="linear", settleCoin="USDT")
    positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
    if not positions:
        return f"🟢 {label}: No open positions to close."
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
    msg = f"🚨 *{label} CLOSE ALL*\n\n✅ Closed {closed_count} position(s)\n"
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



# -- Binance helpers ----------------------------------------------------------

def _get_binance_connector(env_vars: dict):
    import sys as _sys
    _sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
    from exchange.binance_connector import BinanceConnector
    testnet_raw = str(env_vars.get("BINANCE_TESTNET", "false")).strip().lower()
    return BinanceConnector(
        api_key=env_vars.get("BINANCE_API_KEY"),
        api_secret=env_vars.get("BINANCE_API_SECRET"),
        testnet=(testnet_raw == "true"),
    )


def format_binance_balance(account: dict) -> str:
    """Render the Binance Futures USDT balance block for one account.
    Total/free/used are derived from the loader's ``raw`` ccxt-style
    balance map (preserves today's UX)."""
    label = get_strategy_label(_account_env(account))
    payload = dl.account_balance(account)
    if payload is None:
        return f"💰 *{label} Balance (Binance)*\n⚠️ Error: balance unavailable."
    raw = (payload or {}).get("raw") or {}
    if not raw:
        return f"💰 *{label} Balance (Binance)*\nNo data returned."
    usdt = raw.get("USDT", {}) if isinstance(raw, dict) else {}
    total = float((usdt or {}).get("total", 0) or 0)
    free = float((usdt or {}).get("free", 0) or 0)
    used = float((usdt or {}).get("used", 0) or 0)
    return (
        f"💰 *{label} Balance (Binance Futures)*\n"
        f"USDT Total: {total:.2f}\n"
        f"USDT Free: {free:.2f}\n"
        f"USDT Used: {used:.2f}"
    )


def format_binance_positions(account: dict) -> str:
    """Render the Binance open-positions block for one account using
    ``dl.account_open_positions`` output."""
    label = get_strategy_label(_account_env(account))
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
        "Commands:\n"
        "/halt — Stop order placement immediately\n"
        "/resume — Re-enable order placement\n"
        "/status — Kill-switch state, P&L summary, service status\n"
        "/balance — Account balance\n"
        "/trades — Open positions\n"
        "/closeall — Emergency close all positions\n"
        "/log — Recent trader logs\n"
        "/toggle — Start or stop the trader service\n"
        "/download\\_journal — Download trade journal DB\n"
        "/last5 — Last 5 trade signals\n"
        "/backtest — Start backtest in background\n"
        "/latest\\_backtest — Backtest status/result\n"
        "/price — Current BTC price\n"
        "/help — Show this menu"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    halted = is_halted()
    halt_line = "🔴 *HALTED* — orders blocked" if halted else "🟢 *RUNNING* — orders enabled"
    trade_count, total_pnl = fetch_today_pnl()
    open_count = fetch_open_positions_count()
    label = get_strategy_label()
    text = (
        "✅ *ICT Trading Bot Status*\n\n"
        f"🚦 Kill-switch: {halt_line}\n"
        f"📊 Today's trades: {trade_count} | P&L: ${total_pnl:+.2f}\n"
        f"📂 Open positions (DB): {open_count}\n\n"
        f"🟢 {label} trader: `{get_service_status(LIVE_SERVICE_NAME)}`\n"
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
    label = get_strategy_label(_account_env(account))
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
    label = get_strategy_label(_account_env(account))
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
    # Collect rows from every account; today only the legacy account returns
    # data (trades table has no account_id column yet — tracked follow-up).
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


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        log_text = get_last_logs(lines=20)
        label = get_strategy_label()
        await update.message.reply_text(
            f"📝 *{label} logs*\n```{log_text[-3500:]}```",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not read logs: {e}")


async def cmd_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    current = get_service_status(LIVE_SERVICE_NAME)
    action = "stop" if current == "active" else "start"
    result = toggle_service(LIVE_SERVICE_NAME, action)
    await update.message.reply_text(result, parse_mode="Markdown")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        env_vars = load_account_env()
        if str(env_vars.get("EXCHANGE", "")).lower() != "bybit":
            await update.message.reply_text("⚠️ /closeall currently supports Bybit only.")
            return
        msg = close_all_bybit_positions(env_vars)
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ CRITICAL ERROR in closeall: {e}")


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

    parts = (query.data or "").split(":", 1)
    if not parts or not parts[0]:
        return
    action = parts[0]

    if action == "log":
        try:
            log_text = get_last_logs(lines=20)
            label = get_strategy_label()
            await query.edit_message_text(
                f"📝 *{label} logs*\n```{log_text[-3500:]}```",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"⚠️ Could not read logs: {e}")

    elif action == "toggle":
        current = get_service_status(LIVE_SERVICE_NAME)
        act = "stop" if current == "active" else "start"
        result = toggle_service(LIVE_SERVICE_NAME, act)
        await query.edit_message_text(result, parse_mode="Markdown")

    elif action == "closeall":
        env_vars = load_account_env()
        label = get_strategy_label(env_vars)
        await query.edit_message_text(f"🚨 Closing all {label} positions…")
        try:
            msg = close_all_bybit_positions(env_vars)
            await query.edit_message_text(msg, parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"⚠️ Error: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def post_init(app):
        label = format_target_options()
        commands = [
            BotCommand("start", "Show help"),
            BotCommand("help", "Show help"),
            BotCommand("halt", "Stop order placement immediately"),
            BotCommand("resume", "Re-enable order placement"),
            BotCommand("status", "Kill-switch state, P&L summary, service status"),
            BotCommand("balance", "Account balance"),
            BotCommand("trades", "Open positions"),
            BotCommand("closeall", f"Close all {label} positions"),
            BotCommand("last5", "Last 5 journal entries"),
            BotCommand("backtest", "Run backtest"),
            BotCommand("latest_backtest", "Latest backtest result"),
            BotCommand("log", f"Show {label} trader logs"),
            BotCommand("toggle", f"Start/stop {label} trader"),
            BotCommand("download_journal", "Download trade journal DB"),
            BotCommand("price", "Current BTC price"),
        ]
        await app.bot.set_my_commands(commands)

    application.post_init = post_init
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("halt", cmd_halt))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("balance", cmd_balance))
    application.add_handler(CommandHandler("trades", cmd_trades))
    application.add_handler(CommandHandler("closeall", cmd_closeall))
    application.add_handler(CommandHandler("last5", cmd_last5))
    application.add_handler(CommandHandler("backtest", cmd_backtest))
    application.add_handler(CommandHandler("latest_backtest", cmd_latest_backtest))
    application.add_handler(CommandHandler("log", cmd_log))
    application.add_handler(CommandHandler("toggle", cmd_toggle))
    application.add_handler(CommandHandler("download_journal", cmd_download_journal))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.run_polling()


if __name__ == "__main__":
    main()
