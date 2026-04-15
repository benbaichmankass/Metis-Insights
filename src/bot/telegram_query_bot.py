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

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
# DB_PATH: check env override, then repo root, then src/bot/ (legacy).
_DB_CANDIDATES = [
    os.environ.get("TRADE_JOURNAL_DB", ""),
    os.path.join(REPO_ROOT, "trade_journal.db"),
    os.path.join(BASE_DIR, "trade_journal.db"),
]
DB_PATH = next((p for p in _DB_CANDIDATES if p and os.path.exists(p)),
               os.path.join(REPO_ROOT, "trade_journal.db"))  # default if none exist

LIVE_ENV_PATH = os.path.join(REPO_ROOT, ".env.live")
PAPER_ENV_PATH = os.path.join(REPO_ROOT, ".env.paper")

# backtester.py lives in src/ (one level up from src/bot/)
BACKTESTER_PATH = os.path.join(os.path.dirname(BASE_DIR), "backtester.py")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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


def load_account_env(target: str) -> dict:
    if target == "live":
        path = LIVE_ENV_PATH
    elif target == "paper":
        path = PAPER_ENV_PATH
    else:
        raise ValueError(f"Unknown target: {target}")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Environment file not found for {target}: {path}")
    values = dotenv_values(path)
    return {k: v for k, v in values.items() if v is not None}


def get_bybit_client_from_env(env_vars: dict):
    from pybit.unified_trading import HTTP
    return HTTP(
        testnet=False,
        api_key=env_vars.get("BYBIT_API_KEY"),
        api_secret=env_vars.get("BYBIT_API_SECRET"),
    )


def get_account_label(target: str) -> str:
    return "LIVE" if target == "live" else "PAPER"


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


def get_last_logs_for_target(target: str, lines: int = 20) -> str:
    service_name = f"ict-trader-{target}"
    try:
        output = run_shell_command(
            ["journalctl", "-u", service_name, "-n", str(lines), "--no-pager"]
        )
        return output or f"No logs found for {service_name}."
    except Exception as e:
        return f"Could not read logs for {service_name}: {e}"


def format_bybit_balance(env_vars: dict, target: str) -> str:
    try:
        client = get_bybit_client_from_env(env_vars)
        resp = client.get_wallet_balance(accountType="UNIFIED")
        result_list = resp.get("result", {}).get("list", [])
        if not result_list:
            return f"💰 *{get_account_label(target)} Balance*\nNo balance data returned from Bybit."
        coins = result_list[0].get("coin", [])
        lines = [
            f"{c['coin']}: {float(c['walletBalance']):.4f} (≈ ${float(c.get('usdValue', '0')):.2f})"
            for c in coins
            if float(c.get("walletBalance", 0)) > 0
        ]
        text = "\n".join(lines) if lines else "No non-zero balances found."
        return f"💰 *{get_account_label(target)} Balance*\n{text}"
    except Exception as e:
        return f"💰 *{get_account_label(target)} Balance*\n⚠️ Bybit error: {e}"


def format_bybit_positions(env_vars: dict, target: str) -> str:
    try:
        client = get_bybit_client_from_env(env_vars)
        resp = client.get_positions(category="linear", settleCoin="USDT")
        positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
        if not positions:
            return f"📊 *{get_account_label(target)} Positions*\nNo open positions."
        lines = [
            f"{p['symbol']} {p['side']} | Size: {p['size']} | Entry: ${float(p['avgPrice']):,.2f} | PnL: ${float(p['unrealisedPnl']):+.2f}"
            for p in positions
        ]
        return f"📊 *{get_account_label(target)} Positions*\n" + "\n".join(lines)
    except Exception as e:
        return f"📊 *{get_account_label(target)} Positions*\n⚠️ Bybit error: {e}"


def close_all_bybit_positions(env_vars: dict, target: str) -> str:
    client = get_bybit_client_from_env(env_vars)
    resp = client.get_positions(category="linear", settleCoin="USDT")
    positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
    if not positions:
        return f"🟢 {get_account_label(target)}: No open positions to close."
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
    msg = f"🚨 *{get_account_label(target)} CLOSE ALL*\n\n✅ Closed {closed_count} position(s)\n"
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
        latest = fetch_latest_backtest_result()
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


# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    text = (
        "👋 *ICT Trading Bot*\n\n"
        "Commands:\n"
        "/status — Live and paper runtime status\n"
        "/balance — Account balances\n"
        "/trades — Open positions\n"
        "/closeall live|paper — Emergency close positions\n"
        "/log live|paper — Recent logs\n"
        "/toggle live|paper — Start or stop a trader service\n"
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
    text = (
        "✅ *ICT Trading Bot Status*\n\n"
        f"🟢 Live trader: `{get_service_status('ict-trader-live')}`\n"
        f"🟡 Paper trader: `{get_service_status('ict-trader-paper')}`\n"
        f"🤖 Telegram bot: `{get_service_status('ict-telegram-bot')}`\n"
        f"🕐 {now}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    blocks = []
    for target in ("live", "paper"):
        try:
            env_vars = load_account_env(target)
            exchange = str(env_vars.get("EXCHANGE", "")).lower()
            if exchange == "bybit":
                blocks.append(format_bybit_balance(env_vars, target))
            else:
                blocks.append(
                    f"💰 *{get_account_label(target)} Balance*\n"
                    f"Exchange=`{exchange or 'not set'}` — only Bybit supported."
                )
        except Exception as e:
            blocks.append(f"💰 *{get_account_label(target)} Balance*\n⚠️ {e}")
    await update.message.reply_text("\n\n".join(blocks), parse_mode="Markdown")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    blocks = []
    for target in ("live", "paper"):
        try:
            env_vars = load_account_env(target)
            exchange = str(env_vars.get("EXCHANGE", "")).lower()
            if exchange == "bybit":
                blocks.append(format_bybit_positions(env_vars, target))
            else:
                blocks.append(
                    f"📊 *{get_account_label(target)} Positions*\n"
                    f"Exchange=`{exchange or 'not set'}` — only Bybit supported."
                )
        except Exception as e:
            blocks.append(f"📊 *{get_account_label(target)} Positions*\n⚠️ {e}")
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


async def cmd_last5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        rows = fetch_last_5_trades()
        if not rows:
            await update.message.reply_text("📭 No trades found in trade_journal.db.")
            return
        chart_candidates = [
            os.path.join(BASE_DIR, "ict_complete_chart.html"),
            os.path.join(BASE_DIR, "ict_enhanced_chart.html"),
            os.path.join(BASE_DIR, "swing_chart.html"),
        ]
        available_chart = next((p for p in chart_candidates if os.path.exists(p)), None)
        for row in rows:
            msg = (
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
            await update.message.reply_text(msg, parse_mode="Markdown")
            if available_chart:
                await update.message.reply_document(document=open(available_chart, "rb"))
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load last 5 trades: {e}")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    args = getattr(context, "args", []) or []
    target = args[0].strip().lower() if args and args[0].strip().lower() in ("live", "paper") else None
    if not target:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("📜 Live logs", callback_data="log:live"),
            InlineKeyboardButton("📜 Paper logs", callback_data="log:paper"),
        ]])
        await update.message.reply_text("Please choose an account:", reply_markup=keyboard)
        return
    try:
        log_text = get_last_logs_for_target(target, lines=20)
        await update.message.reply_text(
            f"📝 *{get_account_label(target)} logs*\n```{log_text[-3500:]}```",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not read logs: {e}")


async def cmd_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    args = getattr(context, "args", []) or []
    target = args[0].strip().lower() if args and args[0].strip().lower() in ("live", "paper") else None
    if not target:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟢 Toggle Live", callback_data="toggle:live"),
            InlineKeyboardButton("🟡 Toggle Paper", callback_data="toggle:paper"),
        ]])
        await update.message.reply_text("Choose which trader to toggle:", reply_markup=keyboard)
        return
    service_name = f"ict-trader-{target}"
    current = get_service_status(service_name)
    action = "stop" if current == "active" else "start"
    result = toggle_service(service_name, action)
    await update.message.reply_text(result, parse_mode="Markdown")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    args = getattr(context, "args", []) or []
    target = args[0].strip().lower() if args and args[0].strip().lower() in ("live", "paper") else None
    if not target:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🚨 Close Live", callback_data="closeall:live"),
            InlineKeyboardButton("🚨 Close Paper", callback_data="closeall:paper"),
        ]])
        await update.message.reply_text("Choose account to close all positions:", reply_markup=keyboard)
        return
    try:
        env_vars = load_account_env(target)
        if str(env_vars.get("EXCHANGE", "")).lower() != "bybit":
            await update.message.reply_text(f"⚠️ /closeall for {target} currently supports Bybit only.")
            return
        msg = close_all_bybit_positions(env_vars, target)
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
        latest = fetch_latest_backtest_result()
        if latest:
            await update.message.reply_text(format_backtest_summary(latest), parse_mode="Markdown")
        else:
            await update.message.reply_text(
                f"✅ *Backtest COMPLETED*\nFinished: {BACKTEST_STATUS['finished_at']}", parse_mode="Markdown"
            )
    else:
        latest = fetch_latest_backtest_result()
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
    if len(parts) != 2:
        return
    action, target = parts

    if action == "log":
        try:
            log_text = get_last_logs_for_target(target, lines=20)
            await query.edit_message_text(
                f"📝 *{get_account_label(target)} logs*\n```{log_text[-3500:]}```",
                parse_mode="Markdown",
            )
        except Exception as e:
            await query.edit_message_text(f"⚠️ Could not read logs: {e}")

    elif action == "toggle":
        service_name = f"ict-trader-{target}"
        current = get_service_status(service_name)
        act = "stop" if current == "active" else "start"
        result = toggle_service(service_name, act)
        await query.edit_message_text(result, parse_mode="Markdown")

    elif action == "closeall":
        await query.edit_message_text(f"🚨 Closing all {target.upper()} positions…")
        try:
            env_vars = load_account_env(target)
            msg = close_all_bybit_positions(env_vars, target)
            await query.edit_message_text(msg, parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"⚠️ Error: {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def post_init(app):
        commands = [
            BotCommand("start", "Show help"),
            BotCommand("help", "Show help"),
            BotCommand("status", "Service status"),
            BotCommand("balance", "Account balances"),
            BotCommand("trades", "Open positions"),
            BotCommand("closeall", "Close all positions: live|paper"),
            BotCommand("last5", "Last 5 journal entries"),
            BotCommand("backtest", "Run backtest"),
            BotCommand("latest_backtest", "Latest backtest result"),
            BotCommand("log", "Show logs: live|paper"),
            BotCommand("toggle", "Start/stop trader: live|paper"),
            BotCommand("download_journal", "Download trade journal DB"),
            BotCommand("price", "Current BTC price"),
        ]
        await app.bot.set_my_commands(commands)

    application.post_init = post_init
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
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
