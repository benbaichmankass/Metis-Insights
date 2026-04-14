import os
import logging
import sqlite3
import asyncio
import sys
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv, dotenv_values
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
import requests

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, "..", ".."))
DB_PATH = os.path.join(BASE_DIR, "trade_journal.db")

LIVE_ENV_PATH = os.path.join(REPO_ROOT, ".env.live")
PAPER_ENV_PATH = os.path.join(REPO_ROOT, ".env.paper")

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
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)


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
        SELECT
            id, timestamp, symbol, direction, entry_price, exit_price,
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
        SELECT
            id, run_date, strategy_version, start_date, end_date,
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


def resolve_target_from_args(context):
    args = getattr(context, "args", []) or []
    if not args:
        return None
    target = args[0].strip().lower()
    if target in ("live", "paper"):
        return target
    return None


def require_target_help(command_name: str) -> str:
    return (
        f"Please choose an account:\n"
        f"`/{command_name} live`\n"
        f"`/{command_name} paper`"
    )


def run_shell_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    output = (result.stdout or "") + (result.stderr or "")
    return output.strip()


def get_service_status(service_name: str) -> str:
    try:
        output = run_shell_command(["systemctl", "is-active", service_name])
        return output or "unknown"
    except Exception as e:
        return f"error: {e}"


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
    client = get_bybit_client_from_env(env_vars)
    resp = client.get_wallet_balance(accountType="UNIFIED")
    coins = resp["result"]["list"][0]["coin"]
    lines = [
        f"{c['coin']}: {float(c['walletBalance']):.4f} (≈ ${float(c.get('usdValue', '0')):.2f})"
        for c in coins
        if float(c.get("walletBalance", 0)) > 0
    ]
    text = "\n".join(lines) if lines else "No balance found."
    return f"💰 *{get_account_label(target)} Balance*\n{text}"


def format_bybit_positions(env_vars: dict, target: str) -> str:
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
                category="linear",
                symbol=p["symbol"],
                side=side,
                orderType="Market",
                qty=p["size"],
                reduceOnly=True,
            )
            closed_count += 1
        except Exception as e:
            errors.append(f"{p['symbol']}: {str(e)}")

    msg = f"🚨 *{get_account_label(target)} CLOSE ALL*\n\n"
    msg += f"✅ Closed {closed_count} position(s)\n"
    if errors:
        msg += f"❌ Failed: {len(errors)}\n"
        msg += "Errors:\n" + "\n".join(errors[:5])
    return msg


async def run_backtest_in_background(application: Application):
    global BACKTEST_TASK, BACKTEST_STATUS

    BACKTEST_STATUS["state"] = "running"
    BACKTEST_STATUS["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    BACKTEST_STATUS["finished_at"] = None
    BACKTEST_STATUS["last_error"] = None
    BACKTEST_STATUS["last_stdout_tail"] = None
    BACKTEST_STATUS["last_returncode"] = None

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "backtester.py",
            cwd=BASE_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
            logger.error("Backtest failed: %s", BACKTEST_STATUS["last_error"])

            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    "⚠️ *Backtest failed*\n"
                    f"🕒 Finished: {BACKTEST_STATUS['finished_at']}\n"
                    f"🔢 Return code: {process.returncode}\n"
                    f"```{BACKTEST_STATUS['last_error']}```"
                ),
                parse_mode="Markdown",
            )
            return

        BACKTEST_STATUS["state"] = "completed"

        latest = fetch_latest_backtest_result()
        if latest:
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=format_backtest_summary(latest),
                parse_mode="Markdown",
            )
        else:
            stdout_tail = (stdout_text or "Backtest finished, but no row was found in backtest_results.")[-3000:]
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=(
                    "✅ *Backtest finished*\n"
                    f"🕒 Finished: {BACKTEST_STATUS['finished_at']}\n"
                    f"```{stdout_tail}```"
                ),
                parse_mode="Markdown",
            )

    except Exception as e:
        BACKTEST_STATUS["state"] = "failed"
        BACKTEST_STATUS["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        BACKTEST_STATUS["last_error"] = str(e)
        logger.exception("Background backtest crashed")

        await application.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=(
                "⚠️ *Backtest crashed*\n"
                f"🕒 Finished: {BACKTEST_STATUS['finished_at']}\n"
                f"`{str(e)}`"
            ),
            parse_mode="Markdown",
        )
    finally:
        BACKTEST_TASK = None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    text = (
        "👋 *ICT Trading Bot*\n\n"
        "Commands:\n"
        "/status — Show live and paper runtime status\n"
        "/balance — Show live and paper account balances\n"
        "/trades — Show live and paper open positions\n"
        "/closeall live|paper — Emergency close positions for one account\n"
        "/log live|paper — Show recent logs for one account\n"
        "/last5 — Last 5 trade signals from journal\n"
        "/backtest — Start a backtest in the background\n"
        "/latest_backtest — Show latest backtest status/result\n"
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

    live_status = get_service_status("ict-trader-live")
    paper_status = get_service_status("ict-trader-paper")
    telegram_status = get_service_status("ict-telegram-bot")

    text = (
        "✅ *ICT Trading Bot Status*\n\n"
        f"🟢 Live trader: `{live_status}`\n"
        f"🟡 Paper trader: `{paper_status}`\n"
        f"🤖 Telegram bot: `{telegram_status}`\n"
        f"🕐 {now}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    blocks = []

    try:
        live_env = load_account_env("live")
        if str(live_env.get("EXCHANGE", "")).lower() == "bybit":
            blocks.append(format_bybit_balance(live_env, "live"))
        else:
            blocks.append("💰 *LIVE Balance*\nUnsupported exchange for Telegram balance view.")
    except Exception as e:
        blocks.append(f"💰 *LIVE Balance*\n⚠️ Could not fetch: {e}")

    try:
        paper_env = load_account_env("paper")
        if str(paper_env.get("EXCHANGE", "")).lower() == "bybit":
            blocks.append(format_bybit_balance(paper_env, "paper"))
        else:
            blocks.append("💰 *PAPER Balance*\nTelegram balance view currently supports Bybit only.")
    except Exception as e:
        blocks.append(f"💰 *PAPER Balance*\n⚠️ Could not fetch: {e}")

    await update.message.reply_text("\n\n".join(blocks), parse_mode="Markdown")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": "BTCUSDT"},
            timeout=10,
        )
        price = float(resp.json()["result"]["list"][0]["lastPrice"])
        await update.message.reply_text(f"📈 *BTC/USDT:* ${price:,.2f}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch price: {e}")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    blocks = []

    try:
        live_env = load_account_env("live")
        if str(live_env.get("EXCHANGE", "")).lower() == "bybit":
            blocks.append(format_bybit_positions(live_env, "live"))
        else:
            blocks.append("📊 *LIVE Positions*\nUnsupported exchange for Telegram positions view.")
    except Exception as e:
        blocks.append(f"📊 *LIVE Positions*\n⚠️ Could not fetch: {e}")

    try:
        paper_env = load_account_env("paper")
        if str(paper_env.get("EXCHANGE", "")).lower() == "bybit":
            blocks.append(format_bybit_positions(paper_env, "paper"))
        else:
            blocks.append("📊 *PAPER Positions*\nTelegram positions view currently supports Bybit only.")
    except Exception as e:
        blocks.append(f"📊 *PAPER Positions*\n⚠️ Could not fetch: {e}")

    await update.message.reply_text("\n\n".join(blocks), parse_mode="Markdown")


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
                f"🕒 Time: {row['timestamp']}\n"
                f"💱 Symbol: {row['symbol']}\n"
                f"📈 Direction: {row['direction']}\n"
                f"💰 Entry: {row['entry_price']}\n"
                f"🛑 Stop Loss: {row['stop_loss']}\n"
                f"🎯 TP1: {row['take_profit_1']}\n"
                f"🎯 TP2: {row['take_profit_2']}\n"
                f"🎯 TP3: {row['take_profit_3']}\n"
                f"📦 Size: {row['position_size']}\n"
                f"🧠 Setup: {row['setup_type']} | Bias: {row['bias']} | KZ: {row['killzone']}\n"
                f"📝 Entry reason: {row['entry_reason']}\n"
                f"🚪 Exit reason: {row['exit_reason']}\n"
                f"💵 PnL: {row['pnl']} ({row['pnl_percent']}%)\n"
                f"📌 Status: {row['status']}\n"
                f"📓 Notes: {row['notes']}\n"
                f"🧪 Backtest: {bool(row['is_backtest'])}\n"
                f"🕒 Created: {row['created_at']}"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")

            if available_chart:
                await update.message.reply_document(document=open(available_chart, "rb"))

    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load last 5 trades: {e}")


async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    target = resolve_target_from_args(context)
    if not target:
        await update.message.reply_text(require_target_help("log"), parse_mode="Markdown")
        return

    try:
        log_text = get_last_logs_for_target(target, lines=20)
        await update.message.reply_text(
            f"📝 *{get_account_label(target)} logs*\n```{log_text[-3500:]}```",
            parse_mode="Markdown",
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not read logs: {e}")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    target = resolve_target_from_args(context)
    if not target:
        await update.message.reply_text(require_target_help("closeall"), parse_mode="Markdown")
        return

    try:
        env_vars = load_account_env(target)
        exchange_name = str(env_vars.get("EXCHANGE", "")).lower()

        if exchange_name != "bybit":
            await update.message.reply_text(
                f"⚠️ /closeall for {target} currently supports Bybit only."
            )
            return

        msg = close_all_bybit_positions(env_vars, target)
        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(f"⚠️ CRITICAL ERROR in closeall: {e}")


async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BACKTEST_TASK, BACKTEST_STATUS
    if not is_authorised(update):
        return

    if BACKTEST_STATUS["state"] == "running":
        await update.message.reply_text("⏳ Backtest is already running. Use /latest_backtest to see status.")
        return

    application = context.application
    BACKTEST_TASK = asyncio.create_task(run_backtest_in_background(application))
    await update.message.reply_text("🚀 Backtest started in background. Use /latest_backtest to see status and results.")


async def cmd_latest_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return

    state = BACKTEST_STATUS["state"]

    if state == "running":
        lines = [
            "⏳ *Backtest status: RUNNING*",
            f"Started: {BACKTEST_STATUS['started_at']}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if state == "failed":
        lines = [
            "⚠️ *Backtest status: FAILED*",
            f"Started: {BACKTEST_STATUS['started_at']}",
            f"Finished: {BACKTEST_STATUS['finished_at']}",
            f"Return code: {BACKTEST_STATUS['last_returncode']}",
            f"Error: {BACKTEST_STATUS['last_error']}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    if state == "completed":
        latest = fetch_latest_backtest_result()
        if latest:
            await update.message.reply_text(format_backtest_summary(latest), parse_mode="Markdown")
        else:
            lines = [
                "✅ *Backtest status: COMPLETED*",
                f"Finished: {BACKTEST_STATUS['finished_at']}",
                f"Tail: {BACKTEST_STATUS['last_stdout_tail']}",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    latest = fetch_latest_backtest_result()
    if latest:
        await update.message.reply_text(format_backtest_summary(latest), parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ No backtest is running, and no saved backtest result was found.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in environment")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    async def post_init(app):
        commands = [
            BotCommand("start", "Show help and status"),
            BotCommand("help", "Show help and status"),
            BotCommand("status", "Show live and paper service status"),
            BotCommand("balance", "Show live and paper balances"),
            BotCommand("trades", "Show live and paper open positions"),
            BotCommand("closeall", "Close positions for one account: live|paper"),
            BotCommand("last5", "Show last 5 journal entries"),
            BotCommand("backtest", "Run backtest in background"),
            BotCommand("latest_backtest", "Latest backtest status/result"),
            BotCommand("log", "Show logs for one account: live|paper"),
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
    application.add_handler(CommandHandler("price", cmd_price))

    application.run_polling()


if __name__ == "__main__":
    main()
