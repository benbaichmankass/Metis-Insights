import os
import logging
import sqlite3
import subprocess
from datetime import datetime, timezone

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
import requests

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
BYBIT_API_KEY      = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET   = os.getenv("BYBIT_API_SECRET")

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "trade_journal.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def get_bybit_client():
    from pybit.unified_trading import HTTP
    return HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)

def is_authorised(update: Update) -> bool:
    return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

def fetch_last_5_trades():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id,
            timestamp,
            symbol,
            direction,
            entry_price,
            exit_price,
            stop_loss,
            take_profit_1,
            take_profit_2,
            take_profit_3,
            position_size,
            setup_type,
            killzone,
            bias,
            entry_reason,
            exit_reason,
            pnl,
            pnl_percent,
            status,
            notes,
            is_backtest,
            created_at
        FROM trades
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 5
    """)
    rows = cur.fetchall()
    conn.close()
    return rows

def fetch_latest_backtest_result():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT
            id,
            run_date,
            strategy_version,
            start_date,
            end_date,
            total_trades,
            winning_trades,
            losing_trades,
            win_rate,
            profit_factor,
            expectancy,
            max_drawdown,
            max_drawdown_pct,
            sharpe_ratio,
            total_pnl,
            total_pnl_pct,
            avg_win,
            avg_loss,
            largest_win,
            largest_loss
        FROM backtest_results
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    conn.close()
    return row

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    text = (
        "👋 *ICT Trading Bot*\n\n"
        "Available commands:\n"
        "/status   — Is the bot running?\n"
        "/balance  — Current account balance\n"
        "/trades   — Open positions right now\n"
        "/last5    — Last 5 trade signals from journal\n"
        "/backtest — Start a backtest on current strategy\n"
        "/log      — Last 20 log lines\n"
        "/price    — Current BTC price\n"
        "/help     — Show this menu"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    await update.message.reply_text(
        f"✅ ICT Trading Bot is LIVE on Oracle Cloud!\nMonitoring kill zones...\n🕐 {now}"
    )

async def cmd_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        client = get_bybit_client()
        resp = client.get_wallet_balance(accountType="UNIFIED")
        coins = resp["result"]["list"][0]["coin"]
        lines = [
            f"  {c['coin']}: {float(c['walletBalance']):.4f} (≈ ${float(c.get('usdValue','0')):.2f})"
            for c in coins if float(c.get("walletBalance", 0)) > 0
        ]
        text = "\n".join(lines) if lines else "No balance found."
        await update.message.reply_text(f"💰 *Account Balance:*\n{text}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch balance: {e}")

async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        resp = requests.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": "BTCUSDT"},
            timeout=10
        )
        price = float(resp.json()["result"]["list"][0]["lastPrice"])
        await update.message.reply_text(f"📈 *BTC/USDT:* ${price:,.2f}", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch price: {e}")

async def cmd_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        client = get_bybit_client()
        resp = client.get_positions(category="linear", settleCoin="USDT")
        positions = [p for p in resp["result"]["list"] if float(p.get("size", 0)) > 0]
        if not positions:
            await update.message.reply_text("📊 No open positions right now.")
            return
        lines = [
            f"  {p['symbol']} {p['side']} | Size: {p['size']} | Entry: ${float(p['avgPrice']):,.2f} | PnL: ${float(p['unrealisedPnl']):+.2f}"
            for p in positions
        ]
        await update.message.reply_text("📊 *Open Positions:*\n" + "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch trades: {e}")

async def cmd_last5(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
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
                f"📦 Position Size: {row['position_size']}\n"
                f"🧠 Setup: {row['setup_type']}\n"
                f"⏰ Killzone: {row['killzone']}\n"
                f"🧭 Bias: {row['bias']}\n"
                f"📝 Entry Reason: {row['entry_reason']}\n"
                f"🚪 Exit Reason: {row['exit_reason']}\n"
                f"💵 PnL: {row['pnl']}\n"
                f"📊 PnL %: {row['pnl_percent']}\n"
                f"📌 Status: {row['status']}\n"
                f"🗒 Notes: {row['notes']}\n"
                f"🧪 Backtest Trade: {row['is_backtest']}\n"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")

        if available_chart:
            with open(available_chart, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=os.path.basename(available_chart),
                    caption="📎 Latest available chart file from the repo."
                )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not fetch last 5 trades: {e}")

async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    await update.message.reply_text("🔄 Starting backtest on current strategy...")

    try:
        result = subprocess.run(
            ["python", "backtester.py"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=180
        )

        latest = fetch_latest_backtest_result()

        if result.returncode != 0:
            err = (result.stderr or result.stdout or "Unknown error")[-3000:]
            await update.message.reply_text(f"⚠️ Backtest failed:\n```{err}```", parse_mode="Markdown")
            return

        if latest:
            summary = (
                f"✅ *Backtest complete*\n"
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
            )
            await update.message.reply_text(summary, parse_mode="Markdown")
        else:
            out = (result.stdout or "Backtest finished, but no row was found in backtest_results.")[-3000:]
            await update.message.reply_text(f"✅ Backtest finished.\n```{out}```", parse_mode="Markdown")
    except subprocess.TimeoutExpired:
        await update.message.reply_text("⚠️ Backtest timed out after 180 seconds.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not run backtest: {e}")

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorised(update): return
    try:
        log_file = os.path.join(BASE_DIR, "bot.log")
        if not os.path.exists(log_file) or os.path.getsize(log_file) == 0:
            await update.message.reply_text("📋 Log file is empty.")
            return
        with open(log_file, "r") as f:
            lines = f.readlines()
        last_lines = "".join(lines[-20:])[-3000:]
        await update.message.reply_text(
            f"📋 *Last 20 log lines:*\n```{last_lines}```",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not read log: {e}")

async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start",    "Show this menu"),
        BotCommand("status",   "Is the bot running?"),
        BotCommand("balance",  "Current account balance"),
        BotCommand("trades",   "Open positions right now"),
        BotCommand("last5",    "Show last 5 trade signals"),
        BotCommand("backtest", "Run backtest on current strategy"),
        BotCommand("log",      "Last 20 log lines"),
        BotCommand("price",    "Current BTC price"),
        BotCommand("help",     "Show this menu"),
    ])

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("balance",  cmd_balance))
    app.add_handler(CommandHandler("price",    cmd_price))
    app.add_handler(CommandHandler("trades",   cmd_trades))
    app.add_handler(CommandHandler("last5",    cmd_last5))
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    app.add_handler(CommandHandler("log",      cmd_log))
    print("Telegram Query Bot running. Send /start to begin.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
