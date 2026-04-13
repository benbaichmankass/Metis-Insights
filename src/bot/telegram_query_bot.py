import os
import logging
import sqlite3
import asyncio
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
import requests

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "trade_journal.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# In-memory backtest status
BACKTEST_TASK = None
BACKTEST_STATUS = {
    "state": "idle",  # idle | running | completed | failed
    "started_at": None,
    "finished_at": None,
    "last_error": None,
    "last_stdout_tail": None,
    "last_returncode": None,
}


def get_bybit_client():
    from pybit.unified_trading import HTTP

    return HTTP(
        testnet=False,
        api_key=BYBIT_API_KEY,
        api_secret=BYBIT_API_SECRET,
    )


def is_authorised(update: Update) -> bool:
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)


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


async def run_backtest_in_background(application: Application):
    """
    Run backtester.py as a background subprocess so the bot stays responsive.
    """
    global BACKTEST_TASK, BACKTEST_STATUS

    BACKTEST_STATUS["state"] = "running"
    BACKTEST_STATUS["started_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    BACKTEST_STATUS["finished_at"] = None
    BACKTEST_STATUS["last_error"] = None
    BACKTEST_STATUS["last_stdout_tail"] = None
    BACKTEST_STATUS["last_returncode"] = None

    try:
        process = await asyncio.create_subprocess_exec(
            "python",
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
        BACKTEST_STATUS["finished_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        if process.returncode != 0:
            BACKTEST_STATUS["state"] = "failed"
            BACKTEST_STATUS["last_error"] = (
                stderr_text or stdout_text or "Unknown error"
            )[-2000:]
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
            stdout_tail = (
                stdout_text
                or "Backtest finished, but no row was found in backtest_results."
            )[-3000:]
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
        BACKTEST_STATUS["finished_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
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
        "234
        :\n"
        "/status — Is the bot running?\n"
        "/balance — Current account balance\n"
        "/trades — Open positions right now\n"
        "/closeall — EMERGENCY: Close all open positions\n"
        "/last5 — Last 5 trade signals from journal\n"
        "/backtest — Start a backtest in the background\n"
        "/latest_backtest — Show latest backtest status/result\n"
        "/log — Last 20 log lines\n"
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
    await update.message.reply_text(
        f"✅ ICT Trading Bot is LIVE on Oracle Cloud!\n"
        f"Monitoring kill zones...\n🕐 {now}"
    )


async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        client = get_bybit_client()
        resp = client.get_wallet_balance(accountType="UNIFIED")
        coins = resp["result"]["list"][0]["coin"]
        lines = [
            f"{c['coin']}: {float(c['walletBalance']):.4f} (≈ ${float(c.get('usdValue', '0')):.2f})"
            for c in coins
            if float(c.get("walletBalance", 0)) > 0
        ]
        text = "\n".join(lines) if lines else "No balance found."
        await update.message.reply_text(
            f"💰 *Account Balance:*\n{text}", parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch balance: {e}")


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
        await update.message.reply_text(
            f"📈 *BTC/USDT:* ${price:,.2f}", parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch price: {e}")


async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        client = get_bybit_client()
        resp = client.get_positions(category="linear", settleCoin="USDT")
        positions = [
            p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0
        ]
        if not positions:
            await update.message.reply_text("📊 No open positions right now.")
            return

        lines = [
            f"{p['symbol']} {p['side']} | Size: {p['size']} | "
            f"Entry: ${float(p['avgPrice']):,.2f} | "
            f"PnL: ${float(p['unrealisedPnl']):+.2f}"
            for p in positions
        ]
        await update.message.reply_text(
            "📊 *Open Positions:*\n" + "\n".join(lines), parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch trades: {e}")


async def cmd_last5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        rows = fetch_last_5_trades()
        if not rows:
            await update.message.reply_text(
                "📭 No trades found in trade_journal.db."
            )
            return

        chart_candidates = [
            os.path.join(BASE_DIR, "ict_complete_chart.html"),
            os.path.join(BASE_DIR, "ict_enhanced_chart.html"),
            os.path.join(BASE_DIR, "swing_chart.html"),
        ]
        available_chart = next(
            (p for p in chart_candidates if os.path.exists(p)), None
        )

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
                await update.message.reply_document(
                    document=open(available_chart, "rb")
                )

    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not load last 5 trades: {e}")


318
316
(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update):
        return
    try:
        log_path = "/home/ubuntu/ict-trading-bot/bot.log"
        if not os.path.exists(log_path):
            await update.message.reply_text("No bot.log file found.")
            return
        with open(log_path, "r") as f:
            lines = f.readlines()[-20:]
        await update.message.reply_text(
            "📝 *Last 20 log lines:*\n" + "".join(lines), parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not read log file: {e}")


async def cmd_closeall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Emergency command to close ALL open positions immediately.
    """
    if not is_authorised(update):
        return

    try:
        client = get_bybit_client()
        resp = client.get_positions(category="linear", settleCoin="USDT")
        positions = [
            p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0
        ]

        if not positions:
            await update.message.reply_text("🟢 No open positions to close.")
            return

        closed_count = 0
        errors = []

        for p in positions:
            try:
                # Close position by placing opposite order
                side = "Sell" if p["side"] == "Buy" else "Buy"
                client.place_order(
                    category="linear",
                    symbol=p["symbol"],
                    side=side,
                    orderType="Market",
                    qty=p["size"],
                    reduceOnly=True
                )
                closed_count += 1
            except Exception as e:
                errors.append(f"{p['symbol']}: {str(e)}")

        msg = f"🚨 *EMERGENCY CLOSE ALL*\n\n"
        msg += f"✅ Closed {closed_count} position(s)\n"
        if errors:
            msg += f"❌ Failed: {len(errors)}\n"
            msg += "Errors:\n" + "\n".join(errors[:5])

        await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        await update.message.reply_text(
            f"⚠️ CRITICAL ERROR in closeall: {e}"
        )

async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Start a backtest in the background.
    """
    if not is_authorised(update):
        return

    global BACKTEST_TASK, BACKTEST_STATUS

    if BACKTEST_STATUS["state"] == "running":
        await update.message.reply_text(
            "⏳ A backtest is already running.\n"
            "Use /latest_backtest to see status."
        )
        return

    await update.message.reply_text(
        "🚀 Starting backtest in the background...\n"
        "Use /latest_backtest to see status and results."
    )

    app: Application = context.application
    BACKTEST_TASK = asyncio.create_task(run_backtest_in_background(app))


async def cmd_latest_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show latest backtest status/result quickly.
    """
    if not is_authorised(update):
        return

    state = BACKTEST_STATUS["state"]

    if state == "running":
        await update.message.reply_text(
            "⏳ Backtest is currently *running*...\n"
            f"Started: {BACKTEST_STATUS['started_at']}",
            parse_mode="Markdown",
        )
        return

    latest = fetch_latest_backtest_result()
    if latest:
        text = format_backtest_summary(latest)
    else:
        text = "ℹ️ No backtest results found in backtest_results table yet."

    if state == "failed":
        text += (
            "\n\n⚠️ Last backtest *failed*.\n"
            f"Error: {BACKTEST_STATUS['last_error']}"
        )
    elif state == "completed":
        text += (
            "\n\n✅ Last backtest *completed* successfully.\n"
            f"Finished: {BACKTEST_STATUS['finished_at']}"
        )

    await update.message.reply_text(text, parse_mode="Markdown")

500

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set in .env")

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    commands = [
        BotCommand("start", "Show help and status"),
        BotCommand("help", "Show help and status"),
        BotCommand("status", "Bot status"),
        BotCommand("balance", "Account balance"),
        BotCommand("trades", "Open positions"),
                BotCommand("closeall", "Emergency: Close all open positions"),530
        535
525

        BotCommand("last5", "Last 5 trade signals"),
        BotCommand("backtest", "Start backtest in background"),
        BotCommand("latest_backtest", "Latest backtest status/result"),
        BotCommand("log", "Last 20 log lines"),
        BotCommand("price", "Current BTC price"),
    ]
    application.bot.set_my_commands(commands)

    application.522
(CommandHandler("start", cmd_start))
    application.540
(CommandHandler("help", cmd_help))
    application.536
(CommandHandler("status", cmd_status))
    application.538
(CommandHandler("balance", cmd_balance))
    application.540
(CommandHandler("price", cmd_price))
    application.539
(CommandHandler("trades", cmd_trades))
    application.add_handler(CommandHandler("closeall", cmd_closeall))
    application.add_handler(CommandHandler("last5", cmd_last5))
    application.add_handler(CommandHandler("log", cmd_log))
    application.add_handler(CommandHandler("backtest", cmd_backtest))
    application.add_handler(CommandHandler("latest_backtest", cmd_latest_backtest))

    logger.info("Starting Telegram bot...")
    application.run_polling()


if __name__ == "__main__":
    main()
